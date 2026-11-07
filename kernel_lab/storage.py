"""Controller-only SQLite index and immutable run JSON files."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  target TEXT NOT NULL,
  kernelrelease TEXT NOT NULL,
  started_at TEXT NOT NULL,
  successful INTEGER NOT NULL,
  max_latency_us INTEGER,
  result_path TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_exp ON runs(experiment_id, target);
'''


def init(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def insert(path: Path, results_dir: Path, data: dict) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = results_dir / f'{data["experiment_id"]}-{run_id}.json'
    output.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
    max_values = [x['max_us'] for x in data['measurements'] if x.get('max_us') is not None]
    with init(path) as conn:
        conn.execute('INSERT INTO runs (experiment_id,target,kernelrelease,started_at,successful,max_latency_us,result_path) VALUES (?,?,?,?,?,?,?)',
                     (data['experiment_id'], data['target'], data['kernelrelease'], data['started_at'],
                      int(data['successful']), max(max_values) if max_values else None, str(output)))
    return output


def compare(path: Path, first: str, second: str) -> list[dict]:
    with init(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''SELECT experiment_id,target,COUNT(*) AS samples,
                           MIN(max_latency_us) AS best_max_us, MAX(max_latency_us) AS worst_max_us,
                           AVG(max_latency_us) AS mean_max_us
                           FROM runs WHERE experiment_id IN (?,?) AND successful=1
                           GROUP BY experiment_id,target ORDER BY experiment_id,target''', (first, second))
        return [dict(x) for x in rows]
