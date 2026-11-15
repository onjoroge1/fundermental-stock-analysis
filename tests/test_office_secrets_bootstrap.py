"""The secret helper, checked with a stand-in for the GitHub command. No real secret or login is used."""
import importlib.util
import inspect
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("office_secrets_bootstrap", ROOT / "scripts/office_secrets_bootstrap.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

VALUE = "sk-test-NOT-A-REAL-KEY-0123456789"
REPO = dict(repo="owner/name", environment="Production")


class FakeGh:
    def __init__(self, have=(), list_result=None, set_result=(0, "", "")):
        self.have, self.list_result, self.set_result, self.calls = set(have), list_result, set_result, []

    def __call__(self, arguments, value=None):
        self.calls.append((list(arguments), value))
        if arguments[:2] == ["secret", "list"]:
            return self.list_result or (0, json.dumps([{"name": n} for n in sorted(self.have)]), "")
        return self.set_result


def test_a_missing_secret_is_set_from_the_environment_on_standard_input_only():
    gh = FakeGh(have={"DATABASE_URL"})
    out = helper.bootstrap(["DATABASE_URL", "ANTHROPIC_API_KEY", "FMP_API_KEY"], **REPO, run=gh,
                           environ={"ANTHROPIC_API_KEY": VALUE, "DATABASE_URL": "postgres://not-used"})
    assert out == {"DATABASE_URL": "PRESENT", "ANTHROPIC_API_KEY": "CREATED", "FMP_API_KEY": "MISSING"}
    sets = [c for c in gh.calls if c[0][:2] == ["secret", "set"]]
    assert sets == [(["secret", "set", "ANTHROPIC_API_KEY", "--repo", "owner/name", "--env", "Production"], VALUE)]
    assert not any(VALUE in argument or "postgres" in argument for call in gh.calls for argument in call[0])
    assert not any("--body" in call[0] or "-b" in call[0] for call in gh.calls)


def test_a_present_secret_is_replaced_only_when_named():
    gh = FakeGh(have={"DATABASE_URL", "FMP_API_KEY"})
    environ = {"DATABASE_URL": "new", "FMP_API_KEY": "new"}
    out = helper.bootstrap(["DATABASE_URL", "FMP_API_KEY"], **REPO, replace=("FMP_API_KEY",), run=gh, environ=environ)
    assert out == {"DATABASE_URL": "PRESENT", "FMP_API_KEY": "REPLACED"}
    assert [c[0][2] for c in gh.calls if c[0][1] == "set"] == ["FMP_API_KEY"]
    # asked to replace but given no value: the working secret is left alone
    quiet = FakeGh(have={"FMP_API_KEY"})
    assert helper.bootstrap(["FMP_API_KEY"], **REPO, replace=("FMP_API_KEY",), run=quiet, environ={}) == {"FMP_API_KEY": "PRESENT"}
    assert len(quiet.calls) == 1


def test_check_sets_nothing_and_a_blank_value_counts_as_missing():
    gh = FakeGh(have={"DATABASE_URL"})
    out = helper.bootstrap(["DATABASE_URL", "OPENAI_API_KEY"], **REPO, check=True, run=gh, environ={"OPENAI_API_KEY": VALUE})
    assert out == {"DATABASE_URL": "PRESENT", "OPENAI_API_KEY": "MISSING"} and len(gh.calls) == 1
    blank = FakeGh()
    assert helper.bootstrap(["OPENAI_API_KEY"], **REPO, run=blank, environ={"OPENAI_API_KEY": "   "}) == {"OPENAI_API_KEY": "MISSING"}
    assert len(blank.calls) == 1


def test_a_hidden_prompt_is_used_only_when_asked_for():
    asked = []
    gh = FakeGh()
    out = helper.bootstrap(["OPENAI_API_KEY"], **REPO, run=gh, environ={}, ask=lambda text: asked.append(text) or VALUE)
    assert out == {"OPENAI_API_KEY": "MISSING"} and asked == []
    out = helper.bootstrap(["OPENAI_API_KEY"], **REPO, run=gh, environ={}, prompt=True,
                           ask=lambda text: asked.append(text) or VALUE)
    assert out == {"OPENAI_API_KEY": "CREATED"} and asked == ["OPENAI_API_KEY (input hidden): "]
    assert "getpass.getpass" in inspect.getsource(helper.bootstrap) and "input(" not in inspect.getsource(helper)


@pytest.mark.parametrize("failure, status", [((1, "", "HTTP 403: Resource not accessible by integration"), "BLOCKED_PERMISSION"),
                                             ((1, "", "must have admin rights to Repository"), "BLOCKED_PERMISSION"),
                                             ((1, "", "could not resolve host"), "FAILED"), ((127, "", ""), "FAILED")])
def test_a_refusal_by_github_is_reported_as_a_status_for_listing_and_for_setting(failure, status):
    assert helper.bootstrap(["DATABASE_URL", "FMP_API_KEY"], **REPO, run=FakeGh(list_result=failure), environ={}) == {
        "DATABASE_URL": status, "FMP_API_KEY": status}
    assert helper.bootstrap(["FMP_API_KEY"], **REPO, run=FakeGh(set_result=failure), environ={"FMP_API_KEY": VALUE}) == {
        "FMP_API_KEY": status}
    assert helper.bootstrap(["FMP_API_KEY"], **REPO, run=FakeGh(list_result=(0, "not json", "")), environ={}) == {"FMP_API_KEY": "FAILED"}


@pytest.mark.parametrize("names, extra", [(["GITHUB_TOKEN"], {}), (["CRON_SECRET"], {}), ([], {}), (["FMP_API_KEY", "FMP_API_KEY"], {}),
                                          (["FMP_API_KEY"], {"replace": ("DATABASE_URL",)}), (["FMP_API_KEY"], {"replace": ("AWS_KEY",)})])
def test_only_listed_names_are_accepted_and_nothing_is_called_otherwise(names, extra):
    gh = FakeGh()
    with pytest.raises(ValueError, match="NAME_NOT_ALLOWED"):
        helper.bootstrap(names, **REPO, run=gh, environ={}, **extra)
    assert gh.calls == []
    for bad in (dict(repo="owner/name; rm -rf", environment="Production"), dict(repo="owner/name", environment="Prod uction"),
                dict(repo="--repo", environment="Production")):
        with pytest.raises(ValueError, match="REPOSITORY_OR_ENVIRONMENT_INVALID"):
            helper.bootstrap(["FMP_API_KEY"], **bad, run=gh, environ={})


def test_the_command_line_prints_one_status_per_name_and_never_a_value(monkeypatch, capsys):
    gh = FakeGh(have={"DATABASE_URL"}, set_result=(1, "", f"rejected value {VALUE}"))
    monkeypatch.setattr(helper, "gh", gh)
    monkeypatch.setenv("ANTHROPIC_API_KEY", VALUE)
    monkeypatch.setitem(helper.bootstrap.__kwdefaults__, "run", gh)
    code = helper.main(["--repo", "owner/name", "DATABASE_URL", "ANTHROPIC_API_KEY"])
    printed = capsys.readouterr()
    assert printed.out == "DATABASE_URL: PRESENT\nANTHROPIC_API_KEY: FAILED\n" and printed.err == "" and code == 1
    assert VALUE not in printed.out
    with pytest.raises(SystemExit):
        helper.main(["--repo", "owner/name", "GITHUB_TOKEN"])
    assert VALUE not in capsys.readouterr().err


def test_the_helper_writes_no_file_prints_no_gh_output_and_uses_the_standard_library_only():
    source = inspect.getsource(helper)
    assert "open(" not in source and "write" not in source.replace("written", "") and "logging" not in source
    assert "shell=True" not in source and "capture_output=True" in source and "--body" not in source
    imports = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "argparse", "getpass", "json", "os", "re", "subprocess", "sys"}
    assert set(helper.ALLOWED) == {"DATABASE_URL", "OFFICE_DATABASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                                   "FMP_API_KEY", "MASSIVE_API_KEY"}
