"""Strict controller-side configuration and result contracts."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SAFE = re.compile(r'^[a-z][a-z0-9-]{1,47}$')
SHA = re.compile(r'^[a-fA-F0-9]{40}$')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Target(Strict):
    host: str = Field(min_length=1)
    user: str = Field(pattern=r'^[a-z_][a-z0-9_-]*$')
    ssh_port: int = Field(default=22, ge=1, le=65535)
    board: Literal['pi3', 'pi4', 'pi5']
    distro: Literal['raspberrypi-os', 'ubuntu']
    boot_mode: Literal['rpi-tryboot', 'manual']
    power_cycle_argv: list[str] = Field(default_factory=list)
    rescue_select_argv: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def compatible(self):
        if self.boot_mode == 'rpi-tryboot' and self.distro != 'raspberrypi-os':
            raise ValueError('rpi-tryboot currently supports Raspberry Pi OS only')
        return self


class Inventory(Strict):
    targets: dict[str, Target]

    @field_validator('targets')
    @classmethod
    def names(cls, value):
        if not value or any(not SAFE.fullmatch(x) for x in value):
            raise ValueError('target names must be lowercase slugs')
        return value


class BenchmarkSpec(Strict):
    duration_seconds: int = Field(default=60, ge=5, le=86400)
    repetitions: int = Field(default=3, ge=1, le=100)
    priority: int = Field(default=80, ge=1, le=95)
    interval_us: int = Field(default=1000, ge=50, le=1_000_000)
    load: Literal['idle', 'cpu-stress'] = 'idle'
    stress_workers: int = Field(default=2, ge=1, le=32)


class Experiment(Strict):
    name: str
    target: str
    source_dir: Path
    source_commit: str
    defconfig: Literal['bcm2711_defconfig', 'bcm2712_defconfig']
    fragments: list[str] = Field(min_length=1)
    benchmark: BenchmarkSpec = Field(default_factory=BenchmarkSpec)

    @field_validator('name', 'target')
    @classmethod
    def slug(cls, value):
        if not SAFE.fullmatch(value):
            raise ValueError('must be a lowercase slug (2-48 chars)')
        return value

    @field_validator('source_commit')
    @classmethod
    def commit_sha(cls, value):
        if not SHA.fullmatch(value):
            raise ValueError('must specify an exact 40-character kernel commit')
        return value.lower()

    @field_validator('fragments')
    @classmethod
    def config_paths(cls, value):
        for item in value:
            path = Path(item)
            if path.is_absolute() or '..' in path.parts or path.suffix != '.cfg':
                raise ValueError(f'unsafe config fragment path: {item}')
        return value


class ArtifactManifest(Strict):
    schema_version: Literal[1] = 1
    experiment_id: str
    experiment_name: str
    target: str
    source_commit: str
    defconfig: str
    kernelrelease: str
    localversion: str
    image_sha256: str
    modules_sha256: str
    config_sha256: str
    dtbs_sha256: str
    docker_image: str
    docker_image_id: str
    built_at: datetime
    fragment_names: list[str]
    # All hashes are verified again on target before installation.


class LatencyMeasurement(Strict):
    repetition: int = Field(ge=1)
    min_us: float | None = Field(default=None, ge=0)
    avg_us: float | None = Field(default=None, ge=0)
    max_us: float | None = Field(default=None, ge=0)
    p99_us: float | None = Field(default=None, ge=0)
    p999_us: float | None = Field(default=None, ge=0)
    sample_count: int | None = Field(default=None, ge=0)


class RawBenchmarkOutput(Strict):
    repetition: int = Field(ge=1)
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class BenchmarkResult(Strict):
    schema_version: Literal[1] = 1
    experiment_id: str
    target: str
    started_at: datetime
    completed_at: datetime
    kernelrelease: str
    profile: BenchmarkSpec
    successful: bool
    measurements: list[LatencyMeasurement]
    environment: dict
    raw_outputs: list[RawBenchmarkOutput]
    failure: str | None = None
