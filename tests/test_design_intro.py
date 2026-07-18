# -*- coding: utf-8 -*-

from mind_core.design import facade


def test_intro_reveals_prompt_title_and_version(monkeypatch) -> None:
    """启动动画依次显示提示符、标题和版本号。"""
    frames = []
    printed = []

    class FakeLive:
        def __init__(self, renderable, **_kwargs) -> None:
            frames.append(renderable)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def update(self, renderable) -> None:
            frames.append(renderable)

    class FakeConsole:
        def print(self, renderable=None) -> None:
            printed.append(renderable)

    monkeypatch.setattr(facade, "Live", FakeLive)
    monkeypatch.setattr(facade.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(facade.Design, "console", FakeConsole())

    facade.Design.show_intro()

    assert [frame.plain for frame in frames] == [
        ">_",
        "> ",
        ">_",
        ">_ Mind",
        ">_ Mind",
        ">_ Mind",
        ">_ Mind",
        ">_ Mind",
        ">_ Mind (v1.1.7)",
    ]
    assert [
        [(span.start, span.end, span.style) for span in frame.spans[1:]]
        for frame in frames[3:8]
    ] == [
        [(3, 7, "dim")],
        [(3, 4, "bold bright_white"), (4, 7, "dim")],
        [(3, 5, "bold bright_white"), (5, 7, "dim")],
        [(3, 6, "bold bright_white"), (6, 7, "dim")],
        [(3, 7, "bold bright_white")],
    ]
    assert printed[0].plain == ">_ Mind (v1.1.7)"
    assert printed[1] is None


def test_intro_final_line_uses_selected_styles(monkeypatch) -> None:
    """启动标识保持提示符和版本弱化、标题高亮加粗。"""
    printed = []

    class FakeLive:
        def __init__(self, _renderable, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def update(self, _renderable) -> None:
            pass

    class FakeConsole:
        def print(self, renderable=None) -> None:
            printed.append(renderable)

    monkeypatch.setattr(facade, "Live", FakeLive)
    monkeypatch.setattr(facade.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(facade.Design, "console", FakeConsole())

    facade.Design.show_intro()

    final = printed[0]
    assert [(span.start, span.end, span.style) for span in final.spans] == [
        (0, 2, "dim"),
        (3, 7, "bold bright_white"),
        (7, 16, "dim"),
    ]
