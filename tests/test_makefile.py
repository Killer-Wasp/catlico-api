import subprocess


def test_make_dev_supplies_local_postgres_defaults() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "dev"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "POSTGRES_SERVER:=localhost" in result.stdout
    assert "POSTGRES_USER:=catlico" in result.stdout
    assert "POSTGRES_DB:=catlico" in result.stdout
