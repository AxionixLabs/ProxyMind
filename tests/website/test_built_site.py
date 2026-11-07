from pathlib import Path

from website.mind.scripts.check_site import validate_site


def write_page(path: Path, body: str, *, language: str = "zh-CN") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<html lang="{language}"><head>'
        '<link rel="canonical" href="https://www.appserverx.com/">'
        f'</head><body>{body}</body></html>', encoding="utf-8",
    )


def test_build_validation_accepts_unicode_and_encoded_fragments(tmp_path: Path) -> None:
    write_page(tmp_path / "index.html", '<a href="/guide/#审批">指南</a>')
    write_page(
        tmp_path / "guide" / "index.html",
        '<h2 id="审批">审批</h2><a href="#%E5%AE%A1%E6%89%B9">锚点</a>'
        '<a href="https://example.com/missing/">外部链接</a>'
        '<script src="/assets/app.js"></script>',
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("", encoding="utf-8")

    assert validate_site(tmp_path) == ()


def test_build_validation_rejects_links_resources_and_duplicate_ids(tmp_path: Path) -> None:
    write_page(
        tmp_path / "index.html",
        '<a href="/guide/#missing">失效章节</a><img src="/missing.svg">'
        '<a href="../outside.html">越界</a>',
    )
    write_page(tmp_path / "guide" / "index.html", '<h2 id="same"></h2><span id="same"></span>')

    errors = validate_site(tmp_path)

    assert "broken anchor: index.html -> /guide/#missing" in errors
    assert "broken file link: index.html -> /missing.svg" in errors
    assert "duplicate anchor: guide/index.html -> #same" in errors
    assert "link outside site: index.html -> ../outside.html" in errors


def test_build_validation_rejects_missing_home_and_wrong_locale(tmp_path: Path) -> None:
    assert validate_site(tmp_path) == ("built homepage missing",)
    write_page(tmp_path / "index.html", "", language="en")
    assert validate_site(tmp_path) == ("incorrect page language: index.html -> en",)
