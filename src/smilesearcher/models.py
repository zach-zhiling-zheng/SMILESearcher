# SPDX-License-Identifier: GPL-3.0-only
# Modified 2026-09-27 by Zheng Lab; derived from StarLiu714/SMILESearcher.
"""Results shared by individual and batch resolution."""

from dataclasses import asdict, dataclass, field


@dataclass
class Resolution:
    name: str
    smiles: str = ""
    source: str = ""
    status: str = "not_found"
    note: str = ""
    trace: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
