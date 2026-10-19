"""Запускает все тесты по очереди (каждый в своём процессе: у них отдельные базы).

    python -m cargo_platform.tests.run_all
"""
import subprocess
import sys

for module in ("cargo_platform.tests.test_migrations", "cargo_platform.tests.test_week1", "cargo_platform.tests.test_full", "cargo_platform.tests.test_live_file", "cargo_platform.tests.test_webhook"):
    print(f"\n=== {module} ===")
    if subprocess.call([sys.executable, "-m", module]) != 0:
        sys.exit(1)
print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")
