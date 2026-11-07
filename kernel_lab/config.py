"""Parse operator-owned YAML with strict Pydantic models."""
from pathlib import Path
import yaml
from .models import Inventory, Experiment


def load_inventory(path: Path) -> Inventory:
    return Inventory.model_validate(yaml.safe_load(path.read_text()))


def load_experiment(path: Path) -> Experiment:
    return Experiment.model_validate(yaml.safe_load(path.read_text()))
