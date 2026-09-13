# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""The standalone skill must retain its license after installation and upgrades."""

from pathlib import Path
from zipfile import Path as ZipPath
from zipfile import ZipFile

import pytest
import yaml

from gitstow import __version__
from gitstow.cli import main, skill_cmd


@pytest.fixture
def target(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    monkeypatch.setattr(skill_cmd, "CLAUDE_SKILLS_DIR", skills)
    monkeypatch.setattr(skill_cmd, "SKILL_TARGET", skills / "gitstow")
    monkeypatch.setattr(main, "CLAUDE_SKILLS_DIR", skills)
    return skills / "gitstow"


@pytest.mark.parametrize("zipped", [False, True])
def test_install_includes_license_and_valid_skill_frontmatter(target, tmp_path, monkeypatch, zipped):
    source = skill_cmd.get_skill_source_dir()
    contents = {name: (source / name).read_bytes() for name in ("SKILL.md", "LICENSE")}
    with ZipFile(tmp_path / "skill.zip", "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    with ZipFile(tmp_path / "skill.zip") as archive:
        if zipped:
            monkeypatch.setattr(skill_cmd, "get_skill_source_dir", lambda: ZipPath(archive))
        assert skill_cmd._do_install_skill(quiet=True)
    for name, content in contents.items():
        assert (target / name).read_bytes() == content
    assert (target / ".version").read_text() == __version__
    text = (target / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["name"] == "gitstow"
    assert metadata["license"] == "GPL-3.0-or-later"


@pytest.mark.parametrize("complete", [False, True])
def test_auto_upgrade_only_marks_complete_licensed_install(target, tmp_path, monkeypatch, complete):
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("old skill")
    (target / ".version").write_text("0.7.2")
    if not complete:
        source = tmp_path / "incomplete"
        source.mkdir()
        (source / "SKILL.md").write_text("new skill without license")
        monkeypatch.setattr(skill_cmd, "get_skill_source_dir", lambda: source)
    main._auto_update_skill()
    if complete:
        assert (target / ".version").read_text() == __version__
        assert (target / "LICENSE").is_file()
        assert "GPL-3.0-or-later" in (target / "SKILL.md").read_text(encoding="utf-8")
    else:
        assert (target / ".version").read_text() == "0.7.2"
        assert (target / "SKILL.md").read_text() == "old skill"


def test_standalone_license_copies_match_package_notices():
    root = Path(__file__).resolve().parents[1]
    license_text = (root / "LICENSE").read_bytes()
    assert (root / "src/gitstow/skill/LICENSE").read_bytes() == license_text
    assert (root / "site/LICENSE.txt").read_bytes() == license_text
    for name in ("OFL-bricolage-grotesque.txt", "OFL-jetbrains-mono.txt"):
        assert (root / "site/assets/fonts" / name).read_bytes() == (
            root / "src/gitstow/web/static/fonts" / name
        ).read_bytes()
