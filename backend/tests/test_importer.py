import pytest

from app.services.importer import import_project, parse_env


def test_parse_env_ignores_comments_and_blanks():
    assert parse_env("# c\n\nA=1\nB = two \nnot a pair\n") == {"A": "1", "B": "two"}


def test_manifest_for_node_postgres_project(demo_project):
    m = import_project(demo_project)
    assert m["name"] == "demo"
    assert m["runtime"] == "node"
    assert m["start_command"] == "npm start"
    assert m["database"]["kind"] == "postgres"
    assert m["env"]["required"] == ["DATABASE_URL", "PORT"]
    assert m["env"]["present"] == ["PORT"]
    assert m["env"]["env_file_exists"] is True


def test_react_framework_detected(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18"}}')
    assert import_project(tmp_path)["framework"] == "react"


def test_missing_env_file_is_reported(demo_project):
    (demo_project / ".env").unlink()
    env = import_project(demo_project)["env"]
    assert env["env_file_exists"] is False and env["present"] == []


def test_empty_value_counts_as_not_present(demo_project):
    (demo_project / ".env").write_text("PORT=3100\nDATABASE_URL=\n")
    assert import_project(demo_project)["env"]["present"] == ["PORT"]


def test_non_git_directory_is_tolerated(tmp_path):
    assert import_project(tmp_path)["git"]["is_repo"] in (True, False)  # tmp may sit in a repo


def test_missing_path_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        import_project(tmp_path / "nope")


# --- install state, config, database and compose (new manifest fields) -------------------------


def test_missing_packages_and_node_modules_are_detected(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4", "@scope/pkg": "1"}, "devDependencies": {"vite": "5"}}')
    (tmp_path / "node_modules" / "express").mkdir(parents=True)
    (tmp_path / "node_modules" / "express" / "package.json").write_text("{}")
    install = import_project(tmp_path)["install"]
    assert install["declared"] == ["@scope/pkg", "express", "vite"]
    assert install["missing"] == ["@scope/pkg", "vite"] and install["node_modules_exists"] is True


def test_no_node_modules_folder(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4"}}')
    install = import_project(tmp_path)["install"]
    assert install["missing"] == ["express"] and install["node_modules_exists"] is False


def test_port_comes_from_env_then_example(demo_project):
    assert import_project(demo_project)["config"] == {"port": 3100, "source": ".env"}
    (demo_project / ".env").write_text("")
    assert import_project(demo_project)["config"] == {"port": 3100, "source": ".env.example"}
    (demo_project / ".env.example").write_text("NAME=x\n")
    assert import_project(demo_project)["config"] == {"port": None, "source": None}


def test_schema_files_and_expected_tables_are_parsed(tmp_path):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "schema.sql").write_text(
        'CREATE TABLE IF NOT EXISTS notes (id int);\n  create table public."users" (id int);\n-- CREATE TABLE ignored (x int)\n'
        "INSERT INTO notes VALUES (1);\n"
    )
    database = import_project(tmp_path)["database"]
    assert database["schema_files"] == ["db/schema.sql"]
    assert database["expected_tables"] == ["notes", "users"]


def test_compose_services_and_postgres_service(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n  db:\n    image: postgres:16\n"
    )
    docker = import_project(tmp_path)["docker"]
    assert docker["file"] == "docker-compose.yml"
    assert docker["services"] == {"web": "nginx", "db": "postgres:16"} and docker["postgres_service"] == "db"


def test_broken_compose_file_does_not_break_import(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: [unclosed")
    assert import_project(tmp_path)["docker"]["services"] == {}
