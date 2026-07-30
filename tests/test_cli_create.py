from tests.conftest import make_definition

from edutap.db_definitions.cli import main


def test_create_writes_the_file(installed, tmp_path, capsys):
    installed([make_definition("pkg.a", "table_a")])
    target = tmp_path / "create.sql"
    assert main(["create", "--out", str(target)]) == 0
    assert "CREATE TABLE IF NOT EXISTS table_a" in target.read_text()


def test_create_prints_to_stdout_without_out(installed, capsys):
    installed([make_definition("pkg.a", "table_a")])
    assert main(["create"]) == 0
    assert "CREATE TABLE IF NOT EXISTS table_a" in capsys.readouterr().out


def test_create_split_writes_one_file_per_package(installed, tmp_path):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert main(["create", "--split", str(tmp_path)]) == 0
    assert (tmp_path / "pkg.a.sql").exists()
    assert (tmp_path / "pkg.b.sql").exists()


def test_create_honours_packages_and_ddl_role(installed, tmp_path):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    target = tmp_path / "create.sql"
    assert main(["create", "--out", str(target), "--packages", "pkg.b", "--ddl-role", "ddl"]) == 0
    content = target.read_text()
    assert "table_b" in content
    assert "table_a" not in content
    assert "SET ROLE ddl;" in content


def test_create_fails_on_a_contract_violation(installed, capsys):
    installed([make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")])
    assert main(["create"]) == 1
    assert "table_collision" in capsys.readouterr().err
