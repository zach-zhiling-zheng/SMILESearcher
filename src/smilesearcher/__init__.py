"""Chemical name-to-SMILES resolution for MOF and building-block tables."""

from .models import Resolution
from .chemistry import molecular_weight
from .pipeline import run_pipeline
from .resolvers import Resolver

__version__ = "0.2.0"
__all__ = ["Resolution", "Resolver", "run_pipeline", "molecular_weight"]
