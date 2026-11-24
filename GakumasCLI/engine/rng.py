# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
MASK32 = 0xFFFFFFFF


class ExamRandom:
    def __init__(self, state):
        self.state = state & MASK32
        self.state_before = self.state
        self.state_after = self.state

    def next_u32(self):
        self.state_before = self.state
        value = self.state
        value ^= (value << 13) & MASK32
        value ^= value >> 17
        value ^= (value << 5) & MASK32
        self.state = value & MASK32
        self.state_after = self.state
        return self.state

    def range(self, minimum, maximum):
        old_state = self.state
        width = (maximum - minimum) & MASK32
        result = minimum + ((old_state * width) >> 32)
        self.next_u32()
        self.state_before = old_state
        self.state_after = self.state
        return result
