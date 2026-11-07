from kernel_lab.storage import insert, compare


def test_storage(tmp_path):
    db = tmp_path / 'db.sqlite3'
    result = {'experiment_id': 'trial-a', 'target': 'pi3-os',
              'kernelrelease': '6.12-kl-a', 'started_at': '2026-01-01T00:00:00Z',
              'successful': True, 'measurements': [{'max_us': 50}]}
    outfile = insert(db, tmp_path / 'results', result)
    assert outfile.exists()
    stats = compare(db, 'trial-a', 'trial-b')
    assert stats[0]['worst_max_us'] == 50
    assert stats[0]['samples'] == 1
