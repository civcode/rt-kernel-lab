"""Pure offline Kconfig experiment-matrix generator; does not run builds."""
from itertools import product
from pathlib import Path
import re
import yaml

from .build import ROOT
from .config import load_experiment
from .models import Experiment

SLUG = re.compile(r'^[a-z][a-z0-9-]{1,32}$')
ASSIGN = re.compile(r'^(CONFIG_[A-Z0-9_]+)=(.*)$')
DISABLE = re.compile(r'^# (CONFIG_[A-Z0-9_]+) is not set$')


def fragment_settings(fragment: str):
    path = (ROOT / fragment).resolve(strict=True)
    if not path.is_relative_to(ROOT) or path.suffix != '.cfg':
        raise ValueError(f'invalid fragment: {fragment}')
    values = {}
    for line in path.read_text().splitlines():
        match = ASSIGN.fullmatch(line) or DISABLE.fullmatch(line)
        if match:
            values[match.group(1)] = match.group(2) if match.re is ASSIGN else 'n'
    return values


def expand(matrix_file: Path, output: Path, max_candidates: int = 128):
    config = yaml.safe_load(matrix_file.read_text())
    if set(config) != {'base_experiment', 'factors'}:
        raise ValueError('matrix requires base_experiment and factors only')
    base_path = (ROOT / config['base_experiment']).resolve(strict=True)
    if not base_path.is_relative_to(ROOT):
        raise ValueError('base experiment must be inside repository')
    base = load_experiment(base_path)
    factors = config['factors']
    if not isinstance(factors, dict) or not factors:
        raise ValueError('at least one matrix factor is required')
    choices = []
    for group, variants in factors.items():
        if not SLUG.fullmatch(group) or not isinstance(variants, dict) or not variants:
            raise ValueError(f'invalid matrix factor {group}')
        for variant, fragment in variants.items():
            if not SLUG.fullmatch(variant):
                raise ValueError(f'invalid variant {variant}')
            fragment_settings(fragment)
        choices.append(list(variants.items()))
    count = 1
    for item in choices:
        count *= len(item)
    if count > max_candidates:
        raise ValueError(f'matrix is too large: {count} candidates (max {max_candidates})')
    output.mkdir(parents=True, exist_ok=True)
    generated = []
    for combination in product(*choices):
        suffix = '-'.join(variant for variant, _fragment in combination)
        data = base.model_dump(mode='json')
        data['name'] = f"{base.name}-{suffix}"
        data['fragments'] = [*base.fragments, *(fragment for _variant, fragment in combination)]
        experiment = Experiment.model_validate(data)
        requested = {}
        for fragment in experiment.fragments:
            for option, value in fragment_settings(fragment).items():
                if option in requested and requested[option] != value:
                    raise ValueError(f'contradictory fragment values for {option}: {requested[option]} vs {value}')
                requested[option] = value
        file = output / f'{experiment.name}.yaml'
        if file.exists():
            raise FileExistsError(f'generated experiment already exists: {file}')
        # Write only after all choices are validated: caller may inspect output.
        generated.append((file, experiment))
    for file, experiment in generated:
        file.write_text(yaml.safe_dump(experiment.model_dump(mode='json'), sort_keys=False))
    return [f for f, _ in generated]
