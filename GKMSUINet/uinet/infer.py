# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The network for its users: one screen in, the choice out.  No engine, no search -- onnxruntime and the features."""
from __future__ import annotations

import math

import numpy as np

from .encode import QUANTILES, VALUE_SHIFT


class Policy:
    def __init__(self, onnx_path, threads=1):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
        from .spec import encoder, of_onnx_inputs
        self.obs = of_onnx_inputs(self.sess.get_inputs())
        self.enc = encoder(self.obs)
        self.inputs = [i.name for i in self.sess.get_inputs()]

    def decide(self, obs, greedy=True, rng=None):
        batch = self.enc.encode(obs)
        logit, _adv, value = self.sess.run(["logit", "adv", "value"], {k: batch[k] for k in self.inputs})
        n = len(obs["legal"])
        z = logit[0, :n] - logit[0, :n].max()
        probs = np.exp(z) / np.exp(z).sum()
        k = int(np.argmax(probs)) if greedy else int((rng or np.random.default_rng()).choice(n, p=probs))
        return {"action": list(obs["legal"][k]), "probs": [float(x) for x in probs],
                "score_quantiles": {f"{int(q * 100)}%": int(math.exp(float(v) + VALUE_SHIFT))
                                    for q, v in zip(QUANTILES, value[0])}}
