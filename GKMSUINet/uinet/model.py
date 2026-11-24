# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The network: a set encoder over what the screen shows, scoring every legal action."""
from __future__ import annotations

import torch
from torch import nn

from .encode import G_NUM, INPUTS, N_KINDS, QUANTILES, T_EXT, VALUE_SHIFT  # noqa: F401

SIZES = {"XS": (128, 4, 4), "S": (256, 6, 8), "M": (512, 8, 8), "L": (768, 12, 12)}


def mlp(i, h, o, depth=2):
    layers, d = [], i
    for _ in range(depth - 1):
        layers += [nn.Linear(d, h), nn.GELU()]
        d = h
    layers.append(nn.Linear(d, o))
    return nn.Sequential(*layers)


class UINet(nn.Module):
    def __init__(self, vocab_sizes, size="S", dropout=0.0, obs="v1"):
        """OBS: the input version (uinet.spec): "v1" (Ver1.x) or "v2"."""
        super().__init__()
        from .spec import get
        sp = get(obs)
        self.obs = obs
        d, layers, heads = SIZES[size]
        self.size, self.d = size, d
        ds = d // 2
        self.slot_emb = nn.ModuleList(nn.Embedding(vocab_sizes[sp.slot_field[f]], ds) for f in sp.slot_cat)
        self.slot_num = nn.Linear(len(sp.slot_num), ds)
        self.slot_pos = nn.Embedding(sp.slots, ds)
        self.slot_mlp = mlp(ds, ds, ds)
        self.thing_emb = nn.ModuleList(nn.Embedding(vocab_sizes[sp.thing_field[f]], ds) for f in sp.thing_cat)
        self.thing_num = nn.Linear(sp.n_thing_num, ds)
        self.thing_proj = mlp(3 * ds, d, d)
        self.ext = nn.Linear(sp.t_ext, d)
        self.kind_emb = nn.Embedding(sp.n_kinds, d)
        self.status_emb = nn.Embedding(vocab_sizes["status"], d)
        self.status_num = nn.Linear(3, d)
        self.g_emb = nn.ModuleList(nn.Embedding(vocab_sizes[f], d) for f in sp.g_cat)
        self.heads, self.rel_bits = heads, sp.rel_bits
        if sp.rel_bits:
            self.rel = nn.Parameter(torch.zeros(2, sp.rel_bits, heads))
        self.g_num = mlp(sp.g_num, d, d)
        self.end_tok = nn.Parameter(torch.zeros(d))
        self.type_emb = nn.Embedding(4, d)
        layer = nn.TransformerEncoderLayer(d, heads, 4 * d, dropout=dropout, activation="gelu", batch_first=True,
                                           norm_first=True)
        self.encoder = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(d)
        self.logit = mlp(d, d, 1)
        self.adv = mlp(d, d, 1)
        self.value = mlp(d, d, len(QUANTILES))

    def static(self, t_cat, t_num, s_cat, s_num):
        """What a thing is (its effects and object fields): depends on the card / drink / enchant alone."""
        S = s_cat.shape[-2]
        e = self.slot_num(s_num) + self.slot_pos.weight.view(*([1] * (s_num.dim() - 2)), S, -1)
        for i, emb in enumerate(self.slot_emb):
            e = e + emb(s_cat[..., i])
        present = s_num[..., :1]
        e = self.slot_mlp(e) * present
        pooled_sum = e.sum(-2)
        pooled_max = (e + (present - 1.0) * 1e4).amax(-2) * (present.amax(-2) > 0).to(e.dtype)
        o = self.thing_num(t_num)
        for i, emb in enumerate(self.thing_emb):
            o = o + emb(t_cat[..., i])
        return self.thing_proj(torch.cat([o, pooled_sum, pooled_max], -1))

    def things(self, t_cat, t_num, s_cat, s_num, t_ext, t_kind):
        return self.static(t_cat, t_num, s_cat, s_num) + self.ext(t_ext) + self.kind_emb(t_kind)

    def forward_table(self, table, g_num, g_cat, st_kind, st_num, t_kind, t_idx, t_ext, a_tok, t_rel=None):
        """Training: the things given as indices into TABLE (tc, tn, sc, sn tensors, row 0 = padding); every
        distinct row is encoded once per batch.
        """
        uniq, inv = torch.unique(t_idx, return_inverse=True)
        gather_idx = uniq.to(table[0].device)
        tc, tn, sc, sn = (x[gather_idx].to(t_idx.device) for x in table)
        tc, sc = tc.long(), sc.long()
        sn = sn.float()
        emb = self.static(tc, tn, sc, sn)[inv]
        th = emb + self.ext(t_ext) + self.kind_emb(t_kind)
        return self._rest(g_num, g_cat, st_kind, st_num, t_kind, th, a_tok, t_rel)

    def forward(self, g_num, g_cat, st_kind, st_num, t_kind, t_cat, t_num, s_cat, s_num, t_ext, a_tok, t_rel=None):
        th = self.things(t_cat, t_num, s_cat, s_num, t_ext, t_kind)
        return self._rest(g_num, g_cat, st_kind, st_num, t_kind, th, a_tok, t_rel)

    def _bias(self, t_rel, P, dtype):
        """The attention bias [B, heads, L, L] of the relation bits: bit r of t_rel[b, i, j] adds rel[0, r] (per head)
        to query i -> key j and rel[1, r] to query j -> key i; the first P positions (global, end, status) get none."""
        B, T = t_rel.shape[0], t_rel.shape[1]
        pw = torch.pow(2, torch.arange(self.rel_bits, device=t_rel.device))
        bits = torch.remainder(torch.div(t_rel.unsqueeze(-1), pw, rounding_mode="floor"), 2).to(dtype)
        b = bits @ self.rel[0].to(dtype) + bits.transpose(1, 2) @ self.rel[1].to(dtype)
        H = self.heads
        top = torch.zeros(B, P, P + T, H, dtype=dtype, device=b.device)
        left = torch.zeros(B, T, P, H, dtype=dtype, device=b.device)
        return torch.cat([top, torch.cat([left, b], 2)], 1).permute(0, 3, 1, 2)

    def _encode_biased(self, x, bias):
        """The encoder's layers (pre-norm, GELU, the same weights as nn.TransformerEncoderLayer) with an additive
        attention bias [B, H, L, L]; written out so that it exports to ONNX with dynamic sizes."""
        B, L, d = x.shape
        H = self.heads
        dh = d // H
        for layer in self.encoder.layers:
            y = layer.norm1(x)
            qkv = torch.nn.functional.linear(y, layer.self_attn.in_proj_weight, layer.self_attn.in_proj_bias)
            q, k, v = (t.reshape(B, L, H, dh).transpose(1, 2) for t in qkv.chunk(3, -1))
            att = torch.softmax(q @ k.transpose(-1, -2) * (dh ** -0.5) + bias, -1)
            a = (att @ v).transpose(1, 2).reshape(B, L, d)
            x = x + layer.self_attn.out_proj(a)
            y = layer.norm2(x)
            x = x + layer.linear2(torch.nn.functional.gelu(layer.linear1(y)))
        return x

    def _rest(self, g_num, g_cat, st_kind, st_num, t_kind, th, a_tok, t_rel=None):
        B = g_num.shape[0]
        g = self.g_num(g_num)
        for i, emb in enumerate(self.g_emb):
            g = g + emb(g_cat[:, i])
        g = (g + self.type_emb.weight[0]).unsqueeze(1)
        end = (self.end_tok + self.type_emb.weight[1]).view(1, 1, -1).expand(B, 1, -1)
        st = self.status_emb(st_kind) + self.status_num(st_num) + self.type_emb.weight[2]
        th = th + self.type_emb.weight[3]
        x = torch.cat([g, end, st, th], 1)
        pad = torch.cat([torch.zeros(B, 2, dtype=torch.bool, device=x.device), st_kind == 0, t_kind == 0], 1)
        if self.rel_bits and t_rel is not None:
            bias = self._bias(t_rel, 2 + st_kind.shape[1], x.dtype)
            padf = torch.zeros_like(pad, dtype=x.dtype).masked_fill(pad, float("-inf"))
            h = self.norm(self._encode_biased(x, bias + padf.view(B, 1, 1, -1)))
        else:
            h = self.norm(self.encoder(x, src_key_padding_mask=pad))
        S = st_kind.shape[1]
        pos = torch.where(a_tok >= 0, a_tok + 2 + S, torch.where(a_tok == -1, torch.ones_like(a_tok),
                                                                  torch.zeros_like(a_tok)))
        ha = torch.gather(h, 1, pos.unsqueeze(-1).expand(-1, -1, h.shape[-1]))
        mask = a_tok == -2
        logit = self.logit(ha).squeeze(-1).masked_fill(mask, -1e9)
        adv = self.adv(ha).squeeze(-1).masked_fill(mask, 0.0)
        value = self.value(h[:, 0])
        return logit, adv, value


def vocab_sizes(vocab, obs="v1"):
    from .spec import get
    return {f: vocab.size(f) for f in get(obs).vocab_fields}


def from_checkpoint(ck):
    """A UINet with the checkpoint's weights, size and input version (``obs``, "v1" when absent)."""
    from .spec import vocab
    obs = ck.get("obs", "v1")
    m = UINet(vocab_sizes(vocab(obs), obs), ck["size"], obs=obs)
    m.load_state_dict(ck["state_dict"])
    return m


def to_torch(batch, device="cpu"):
    """The forward's inputs of a collated batch (v2: t_rel last)."""
    return [torch.as_tensor(batch[k], device=device) for k in INPUTS + (("t_rel",) if "t_rel" in batch else ())]


TABLE_INPUTS = ("g_num", "g_cat", "st_kind", "st_num", "t_kind", "t_idx", "t_ext", "a_tok")


def table_tensors(table, device="cpu", chunk=16384, consume=False):
    """CONSUME (a v2 table, uinet.encode2.Rows): every block is released once copied, so the table is held once, not
    twice; the table has no rows afterwards."""
    import numpy as np
    rows = table.rows
    out = []
    if hasattr(rows, "block"):
        firsts = [torch.as_tensor(x) for x in rows[0]]
        for k, first in enumerate(firsts):
            t = torch.empty((len(rows),) + tuple(first.shape), dtype=first.dtype, device=device)
            for c in range(rows.blocks()):
                b = rows.block(k, c, take=consume)
                t[c * rows.CHUNK:c * rows.CHUNK + len(b)] = torch.as_tensor(b, device=device)
            out.append(t)
        return out
    for k in range(4):
        first = torch.as_tensor(rows[0][k])
        t = torch.empty((len(rows),) + tuple(first.shape), dtype=first.dtype, device=device)
        for i in range(0, len(rows), chunk):
            t[i:i + chunk] = torch.as_tensor(np.stack([r[k] for r in rows[i:i + chunk]]), device=device)
        out.append(t)
    return out


def to_torch_compact(batch, device="cpu"):
    return [torch.as_tensor(batch[k], device=device)
            for k in TABLE_INPUTS + (("t_rel",) if "t_rel" in batch else ())]


def n_params(model):
    return sum(p.numel() for p in model.parameters())
