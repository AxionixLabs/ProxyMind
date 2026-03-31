# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import random
import time
import typing
import asyncio
import textwrap
from dataclasses import dataclass
from pathlib import Path
from collections import deque
from rich.cells import cell_len
from rich.live import Live
from rich.text import Text
from rich.tree import Tree
from rich.console import Console
from mind_nova import const


def mix_hex_color(start: str, end: str, ratio: float) -> str:
    start_rgb = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
    clamped = max(0.0, min(1.0, float(ratio)))
    mixed = tuple(
        round(start_rgb[channel] + ((end_rgb[channel] - start_rgb[channel]) * clamped))
        for channel in range(3)
    )
    return f"#{mixed[0]:02X}{mixed[1]:02X}{mixed[2]:02X}"


def ease_in_out_sine(ratio: float) -> float:
    clamped = max(0.0, min(1.0, float(ratio)))
    return 0.5 - 0.5 * math.cos(math.pi * clamped)


@dataclass(frozen=True)
class SweepStatusSpec(object):
    refresh_per_second: int
    phase_rate: float
    text_limit: int
    shell_freq: float
    lead_span: float
    tail_span: float
    peak_radius: float
    near_ratio: float
    mid_ratio: float
    scan_speed: float = 0.0
    scan_pad: float = 0.0
    drift_wobble_amp: float = 0.0
    drift_wobble_freq: float = 0.0
    drift_offset: float = 0.0
    entry_pad: float = 0.0
    exit_pad: float = 0.0


@dataclass(frozen=True)
class ProgressiveStatusSpec(object):
    refresh_per_second: int
    phase_rate: float
    text_limit: int
    shell_freq: float
    head_speed: float
    cycle_padding: float
    head_offset: float
    tail_reset: float
    lead_glow: float
    tail_glow: float


class Design(object):
    """Design class."""

    console: typing.Optional["Console"] = Console()

    def __init__(self, design_level: str = const.SHOW_LEVEL):
        self.design_level = design_level

    class Particle(object):
        """Particle class."""

        __slots__ = ("x", "y", "tx", "ty")

        def __init__(self, x: float, y: float, tx: int, ty: int):
            self.x = x
            self.y = y
            self.tx = tx
            self.ty = ty

    class Doc(object):
        """Doc class."""

        @classmethod
        def log(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"[bold]{text}")

        @classmethod
        def suc(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.SUC}{text}")

        @classmethod
        def wrn(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.WRN}{text}")

        @classmethod
        def err(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.ERR}{text}")

    @staticmethod
    def startup_logo() -> None:
        color = random.choice([
            "#7C3AED",
            "#A855F7",
            "#6366F1",
            "#3B82F6",
            "#22D3EE",
            "#06B6D4",
            "#10B981",
            "#2DD4BF",
            "#F472B6",
            "#FB7185",
            "#FBBF24",
            "#A3E635",
            "#7C3AED",
            "#A855F7",
            "#6366F1",
            "#3B82F6",
            "#60A5FA",
            "#22D3EE",
            "#06B6D4",
            "#2DD4BF",
            "#10B981",
            "#34D399",
            "#0EA5E9",
            "#38BDF8",
        ])

        banner_standard = textwrap.dedent(f"""\
             __  __ _           _
            |  \/  (_)_ __   __| |
            | |\/| | | '_ \ / _` |
            | |  | | | | | | (_| |
            |_|  |_|_|_| |_|\__,_|
        """)
        banner_speed = textwrap.dedent(f"""\
            ______  _______       _________
            ___   |/  /__(_)____________  /
            __  /|_/ /__  /__  __ \  __  /
            _  /  / / _  / _  / / / /_/ /
            /_/  /_/  /_/  /_/ /_/\__,_/
        """)
        banner = random.choice([banner_standard, banner_speed])

        Design.console.print(f"[bold {color}]{banner}")
        Design.console.print(const.DECLARE)

    @staticmethod
    def show_intro() -> None:
        title     = const.APP_DESC
        boot_line = "Starting"

        theme = random.choice([
            {
                "title_idle"  : "#43515A",
                "title_live"  : "#E4EFF4",
                "cursor_live" : "#F4FBFF",
                "status_live" : "#C7D9E1",
                "status_fade" : "#5B6972",
                "ready_live"  : "#C3D6DE",
                "version_dim" : "#7B8991",
                "sep_dim"     : "#56646C",
            },
            {
                "title_idle"  : "#4A4F58",
                "title_live"  : "#F0EADF",
                "cursor_live" : "#FFF7EC",
                "status_live" : "#D5C8B7",
                "status_fade" : "#6A645C",
                "ready_live"  : "#CDBEAB",
                "version_dim" : "#918577",
                "sep_dim"     : "#70675E",
            },
            {
                "title_idle"  : "#3E5253",
                "title_live"  : "#DEF4F1",
                "cursor_live" : "#F2FFFC",
                "status_live" : "#B9DED7",
                "status_fade" : "#587071",
                "ready_live"  : "#A9D4CC",
                "version_dim" : "#728E8B",
                "sep_dim"     : "#526A68",
            },
        ])
        title_idle  = theme["title_idle"]
        title_live  = theme["title_live"]
        cursor_live = theme["cursor_live"]
        status_live = theme["status_live"]
        status_fade = theme["status_fade"]
        ready_live  = theme["ready_live"]
        version_dim = theme["version_dim"]
        sep_dim     = theme["sep_dim"]

        def blend(start: str, end: str, ratio: float) -> str:
            return mix_hex_color(start, end, Design._smoothstep(ratio))

        def boot_status_frame(ratio: float) -> Text:
            line = Text()
            line.append(boot_line, style=f"bold {blend(status_fade, status_live, ratio)}")
            return line

        def ready_status_frame(ratio: float) -> Text:
            line = Text()
            ready_ratio = max(0.0, min(1.0, float(ratio)))
            version_ratio = max(0.0, min(1.0, (ready_ratio - 0.24) / 0.76))
            sep_ratio = max(0.0, min(1.0, (ready_ratio - 0.10) / 0.90))

            line.append("Ready", style=f"bold {blend(status_fade, ready_live, ready_ratio)}")
            line.append(" · ", style=f"bold {blend(status_fade, sep_dim, sep_ratio)}")
            line.append(
                f"v{const.APP_VERSION}",
                style=f"bold {blend(status_fade, version_dim, version_ratio)}"
            )
            return line

        def frame(
            visible: int,
            *,
            cursor_on: bool,
            line: typing.Optional[Text] = None
        ) -> Text:
            out = Text()
            clamped = max(0, min(len(title), int(visible)))
            shown = title[:clamped]
            padding = " " * max(0, len(title) - clamped)

            if shown:
                title_color = mix_hex_color(title_idle, title_live, 0.92)
                out.append(shown, style=f"bold {title_color}")
            if padding:
                out.append(padding, style=f"bold {title_idle}")
            out.append("_" if cursor_on else " ", style=f"bold {cursor_live if cursor_on else title_idle}")

            if line is not None:
                out.append("\n")
                out.append_text(line)

            return out

        with Live(
            frame(0, cursor_on=True),
            console=Design.console,
            refresh_per_second=30,
            transient=True
        ) as live:
            for index in range(1, len(title) + 1):
                live.update(frame(index, cursor_on=True))
                if index == 1:
                    time.sleep(0.052)
                elif index == len(title):
                    time.sleep(0.048)
                else:
                    time.sleep(0.036)

            for cursor_visible, pause in (
                (False, 0.050),
                (True, 0.045),
                (False, 0.040),
                (True, 0.038),
                (False, 0.052),
            ):
                live.update(frame(len(title), cursor_on=cursor_visible))
                time.sleep(pause)

            for boot_ratio in (0.22, 0.54, 0.86, 1.0):
                live.update(
                    frame(
                        len(title),
                        cursor_on=False,
                        line=boot_status_frame(boot_ratio)
                    )
                )
                time.sleep(0.036)

            time.sleep(0.060)

            for fade_ratio in (0.62, 0.28, 0.0):
                live.update(
                    frame(
                        len(title),
                        cursor_on=False,
                        line=boot_status_frame(fade_ratio)
                    )
                )
                time.sleep(0.026)

            for ready_phase in (0.18, 0.42, 0.74, 1.0):
                live.update(
                    frame(
                        len(title),
                        cursor_on=False,
                        line=ready_status_frame(ready_phase)
                    )
                )
                time.sleep(0.044)

            time.sleep(0.22)

        final = Text()
        final.append(title, style=f"bold {title_live}")
        final.append(" · ", style=f"bold {sep_dim}")
        final.append("Ready", style=f"bold {ready_live}")
        final.append(" · ", style=f"bold {sep_dim}")
        final.append(f"v{const.APP_VERSION}", style=f"bold {version_dim}")
        Design.console.print(final)
        Design.console.print()

    @staticmethod
    def show_outro() -> None:
        """退场动画：系统解体风格，打字与 glitch 组合后定格。"""
        title = const.APP_DESC
        theme = random.choice([
            {
                "title_dim"  : "#7A868E",
                "title_live" : "#F3F7FA",
                "noise_dim"  : "#4D5B63",
                "noise_live" : "#9AB0BE",
                "glitch_a"   : "#D5E1E8",
                "glitch_b"   : "#FBFEFF"
            },
            {
                "title_dim"  : "#857667",
                "title_live" : "#FAF1E6",
                "noise_dim"  : "#65594F",
                "noise_live" : "#B89C83",
                "glitch_a"   : "#E4D0BB",
                "glitch_b"   : "#FFF8EE"
            },
            {
                "title_dim"  : "#70857F",
                "title_live" : "#F1FBF7",
                "noise_dim"  : "#506660",
                "noise_live" : "#8FBBAF",
                "glitch_a"   : "#D0E9E0",
                "glitch_b"   : "#F8FFFC"
            },
            {
                "title_dim"  : "#6F7482",
                "title_live" : "#F1F2F8",
                "noise_dim"  : "#4D5160",
                "noise_live" : "#8C94AE",
                "glitch_a"   : "#CDD3E6",
                "glitch_b"   : "#FBFCFF"
            },
            {
                "title_dim"  : "#786D64",
                "title_live" : "#F6EFE8",
                "noise_dim"  : "#5A4F49",
                "noise_live" : "#A98F80",
                "glitch_a"   : "#DBCABF",
                "glitch_b"   : "#FFF9F4"
            }
        ])
        glitch_chars = tuple("|/\\=+-:*#%")

        width = max(38, cell_len(title) + 18)
        core_width = cell_len(title) + 4
        side_width = max(6, (width - core_width) // 2)
        aperture_offsets = {
            "upper" : random.choice((-3, -2, -1)),
            "lower" : random.choice((1, 2, 3)),
        }

        def sample_chars(pool: tuple[str, ...], count: int) -> tuple[str, ...]:
            return tuple(random.sample(pool, k=count))

        def weighted_pick(weighted: tuple[tuple[str, int], ...]) -> str:
            chars   = [char for char, _ in weighted]
            weights = [weight for _, weight in weighted]
            return random.choices(chars, weights=weights, k=1)[0]

        def make_side_profile(*, upper: bool, near_core: bool) -> dict[str, typing.Any]:
            if upper:
                role      = "scan" if near_core else "echo"
                char_pool = ("·", "•", ":", "¦", "=", "-", ".", "˙")
                dust_pool = ("·", ".", ":", "•", "˙")
                primary   = sample_chars(char_pool, 5)
                trail     = sample_chars(char_pool, 3)

                profile = {
                    "role": role,
                    "char_cycle": primary,
                    "dust_chars": dust_pool,
                    "core_guard": random.randint(1, 2),
                    "path_mode": random.choice(("slide", "bounce")) if role == "scan" else random.choice(("slide", "breath")),
                    "path_span": random.randint(1, 2) if role == "scan" else random.randint(1, 3),
                    "path_phase": random.randint(0, 7),
                    "seed_shift": random.randint(0, 7),
                    "anchor": random.uniform(0.64, 0.94) if near_core else random.uniform(0.04, 0.34),
                    "wobble": random.randint(-1, 1),
                    "memory_decay": random.uniform(0.74, 0.84) if role == "scan" else random.uniform(0.64, 0.76),
                    "spawn_gain": random.uniform(0.74, 0.92) if role == "scan" else random.uniform(0.54, 0.72),
                    "dust_rate": random.uniform(0.04, 0.10) if role == "scan" else random.uniform(0.12, 0.20),
                    "rare_rate": random.uniform(0.08, 0.14) if role == "scan" else random.uniform(0.12, 0.20),
                    "sweep_mode": random.choice(("forward", "pingpong")) if role == "scan" else random.choice(("reverse", "pingpong")),
                    "sweep_speed": random.randint(2, 4) if role == "scan" else random.randint(1, 3),
                    "sweep_width": random.randint(4, 6) if role == "scan" else random.randint(2, 4),
                    "sweep_phase": random.randint(0, 8),
                    "glow_bias": random.uniform(0.94, 1.10) if role == "scan" else random.uniform(0.82, 0.98),
                    "near_core_bias": random.uniform(1.18, 1.34) if role == "scan" else random.uniform(1.02, 1.18),
                    "outer_falloff": random.uniform(0.42, 0.58) if role == "scan" else random.uniform(0.24, 0.42),
                    "fade_bias": random.uniform(0.00, 0.03) if role == "scan" else random.uniform(0.02, 0.06),
                    "weighted_chars": (
                        (primary[0], 7), (primary[1], 6), (primary[2], 5), (primary[3], 4), (primary[4], 3),
                        (trail[0], 2), (trail[1], 2), (trail[2], 1),
                    ),
                    "rare_event": random.choice(("spark", "reverse")) if role == "scan" else random.choice(("spark", "collapse")),
                }
                if role == "echo":
                    profile["weighted_chars"] = (
                        (trail[0], 4), (primary[0], 4), (trail[1], 3), (primary[1], 3),
                        (trail[2], 2), (primary[2], 2), (primary[3], 1), (primary[4], 1),
                    )
                return profile

            role      = "fracture" if near_core else "debris"
            char_pool = ("·", "•", ":", "¦", "-", ".", "_", "˙", "⋅")
            dust_pool = ("·", ".", ":", "¦", "-", "•", "˙", "⋅")
            primary   = sample_chars(char_pool, 5)
            trail     = sample_chars(char_pool, 4)

            profile = {
                "role": role,
                "char_cycle": primary,
                "dust_chars": dust_pool,
                "core_guard": random.randint(1, 2),
                "path_mode": random.choice(("bounce", "stutter", "breath")) if role == "fracture" else random.choice(("slide", "stutter", "breath")),
                "path_span": random.randint(2, 3) if role == "fracture" else random.randint(1, 3),
                "path_phase": random.randint(0, 9),
                "seed_shift": random.randint(1, 9),
                "anchor": random.uniform(0.60, 0.92) if near_core else random.uniform(0.08, 0.38),
                "wobble": random.randint(-1, 1),
                "memory_decay": random.uniform(0.54, 0.70) if role == "fracture" else random.uniform(0.60, 0.76),
                "spawn_gain": random.uniform(0.82, 1.02) if role == "fracture" else random.uniform(0.64, 0.86),
                "dust_rate": random.uniform(0.20, 0.32) if role == "fracture" else random.uniform(0.24, 0.38),
                "rare_rate": random.uniform(0.18, 0.30) if role == "fracture" else random.uniform(0.16, 0.26),
                "flash_mode": random.choice(("jump", "pulse")) if role == "fracture" else random.choice(("crawl", "jump", "pulse")),
                "flash_span": random.randint(1, 2) if role == "fracture" else random.randint(1, 3),
                "flash_phase": random.randint(0, 8),
                "glow_bias": random.uniform(0.98, 1.18) if role == "fracture" else random.uniform(0.88, 1.08),
                "near_core_bias": random.uniform(1.20, 1.36) if role == "fracture" else random.uniform(1.08, 1.24),
                "outer_falloff": random.uniform(0.24, 0.40) if role == "fracture" else random.uniform(0.30, 0.48),
                "fade_bias": random.uniform(0.04, 0.08) if role == "fracture" else random.uniform(0.02, 0.06),
                "weighted_chars": (
                    (primary[0], 7), (primary[1], 6), (primary[2], 5), (primary[3], 4), (primary[4], 3),
                    (trail[0], 3), (trail[1], 2), (trail[2], 2), (trail[3], 1),
                ),
                "rare_event": random.choice(("collapse", "pulse_cut")) if role == "fracture" else random.choice(("spark", "collapse", "pulse_cut"))
            }
            if role == "debris":
                profile["weighted_chars"] = (
                    (trail[0], 5), (trail[1], 4), (primary[0], 4), (trail[2], 3), (primary[1], 3),
                    (trail[3], 2), (primary[2], 2), (primary[3], 1), (primary[4], 1)
                )
            return profile

        side_profiles = {
            "upper_left"  : make_side_profile(upper=True, near_core=True),
            "upper_right" : make_side_profile(upper=True, near_core=False),
            "lower_left"  : make_side_profile(upper=False, near_core=True),
            "lower_right" : make_side_profile(upper=False, near_core=False)
        }
        noise_fields: dict[str, dict[str, typing.Any]] = {
            name: {
                "energy"      : [0.0] * side_width,
                "glyph"       : [" "] * side_width,
                "last_tick"   : None,
                "rare_timer"  : 0,
                "rare_window" : None
            }
            for name in side_profiles
        }

        def center_text(inner: Text, *, pad_style: str = "") -> Text:
            used_width = cell_len(inner.plain)

            pad   = max(0, width - used_width)
            left  = pad // 2
            right = pad - left

            line = Text()
            if left:
                line.append(" " * left, style=pad_style)
            line.append_text(inner)
            if right:
                line.append(" " * right, style=pad_style)
            return line

        def trajectory_offset(*, frame_tick: int, phase_shift: int, span: int, mode: str) -> int:
            if span <= 0:
                return 0
            phase_tick = frame_tick + phase_shift
            if mode == "slide":
                period = (span * 2) + 1
                return (phase_tick % period) - span
            if mode == "bounce":
                period   = max(2, span * 4)
                step     = phase_tick % period
                mirrored = step if step <= period // 2 else period - step
                return int(round((mirrored / max(1, period // 2) * span) - span))
            if mode == "stutter":
                return random.choice((-span, 0, span)) if phase_tick % 2 == 0 else random.randint(-span, span)

            period = max(3, (span * 3) + 1)
            wave   = (phase_tick % period) / max(1, period - 1)
            return int(round((0.5 - abs(wave - 0.5)) * 2 * span)) - (span // 2)

        def sweep_position(*, frame_tick: int, sweep_phase: int, sweep_speed: int, sweep_mode: str) -> int:
            if sweep_mode == "reverse":
                return side_width - 1 - ((frame_tick * sweep_speed + sweep_phase) % max(1, side_width))
            if sweep_mode == "pingpong":
                period = max(2, (side_width * 2) - 2)
                step = (frame_tick * sweep_speed + sweep_phase) % period
                mirrored = step if step < side_width else period - step
                return int(mirrored)
            return (frame_tick * sweep_speed + sweep_phase) % max(1, side_width)

        def flash_window(
            *,
            frame_tick: int,
            flash_phase: int,
            flash_mode: str,
            start_pos: int,
            end_pos: int,
            flash_span: int
        ) -> tuple[int, int]:
            width_span = max(1, end_pos - start_pos)
            if flash_mode == "jump":
                base = random.randint(start_pos, max(start_pos, end_pos - 1))
            elif flash_mode == "pulse":
                phase_tick = (frame_tick + flash_phase) % width_span
                base = start_pos + max(0, phase_tick - (flash_span // 2))
            else:
                base = start_pos + max(0, ((frame_tick + flash_phase) % width_span) - flash_span)
            return base, min(end_pos, base + flash_span)

        def refresh_field(
            field_key: str,
            *,
            frame_tick: int,
            activity: float,
            upper: bool
        ) -> tuple[list[float], list[str]]:
            field = noise_fields[field_key]
            if field["last_tick"] == frame_tick:
                return field["energy"], field["glyph"]

            profile = side_profiles[field_key]
            energy: list[float] = field["energy"]
            glyph: list[str] = field["glyph"]
            for cell_index, cell_level in enumerate(energy):
                fade = profile["memory_decay"] - profile["fade_bias"] - (
                    0.03 if cell_index < side_width // 2 else 0.0
                )
                energy[cell_index] = max(0.0, cell_level * fade)
                if energy[cell_index] < 0.09:
                    glyph[cell_index] = " "

            drift = trajectory_offset(
                frame_tick=frame_tick + profile["seed_shift"],
                phase_shift=profile["path_phase"],
                span=profile["path_span"],
                mode=profile["path_mode"],
            )
            active_width = max(
                2, min(side_width, int(
                    round(side_width * max(0.16 if upper else 0.24, activity * profile["spawn_gain"]))
                ))
            )

            slack = max(0, side_width - active_width)

            anchor_start = int(round(slack * profile["anchor"]))

            start = max(0, min(slack, anchor_start + profile["wobble"] + drift))
            end   = min(side_width, start + active_width)

            rare_window = field["rare_window"]
            if field["rare_timer"] <= 0 and random.random() < profile["rare_rate"]:
                rare_size = random.randint(1, min(3, active_width))
                rare_from = random.randint(start, max(start, end - rare_size))
                rare_window = (rare_from, min(end, rare_from + rare_size))
                field["rare_window"] = rare_window
                field["rare_timer"] = random.randint(1, 3)
            elif field["rare_timer"] > 0:
                field["rare_timer"] -= 1
                rare_window = field["rare_window"]
            else:
                field["rare_window"] = None

            scan_peak = None
            flash_from, flash_to = start, start
            if upper:
                scan_peak = sweep_position(
                    frame_tick=frame_tick,
                    sweep_phase=profile["sweep_phase"],
                    sweep_speed=profile["sweep_speed"],
                    sweep_mode=profile["sweep_mode"],
                )
            else:
                flash_from, flash_to = flash_window(
                    frame_tick=frame_tick,
                    flash_phase=profile["flash_phase"],
                    flash_mode=profile["flash_mode"],
                    start_pos=start,
                    end_pos=end,
                    flash_span=profile["flash_span"]
                )

            near_core_indices = range(
                max(0, side_width - 4), side_width
            ) if profile["anchor"] >= 0.5 else range(0, min(4, side_width))

            for cell_index in range(side_width):
                core_distance = (side_width - 1 - cell_index) if profile["anchor"] >= 0.5 else cell_index
                core_ratio = 1.0 - (core_distance / max(1, side_width - 1))
                in_core_guard = core_distance < profile["core_guard"]

                if start <= cell_index < end:
                    inject = activity * profile["spawn_gain"] * (0.54 + (core_ratio * profile["near_core_bias"]))
                    if in_core_guard:
                        inject *= 0.16 if upper else 0.12
                    if upper and scan_peak is not None:
                        sweep_ratio = max(
                            0.0, 1.0 - (abs(cell_index - scan_peak) / max(1, profile["sweep_width"]))
                        )
                        inject += sweep_ratio * (0.42 if in_core_guard else 0.65)
                    if not upper and flash_from <= cell_index < flash_to:
                        inject *= 0.18
                    if rare_window and rare_window[0] <= cell_index < rare_window[1]:
                        if profile["rare_event"] == "collapse":
                            inject *= 0.08
                        elif profile["rare_event"] == "reverse":
                            inject += 0.36
                        else:
                            inject += 0.28
                    energy[cell_index] = min(1.0, max(energy[cell_index], inject))
                    glyph[cell_index]  = weighted_pick(profile["weighted_chars"])

                else:
                    dust_bias = profile["dust_rate"] * (
                        profile["near_core_bias"] if cell_index in near_core_indices else profile["outer_falloff"]
                    )
                    if in_core_guard:
                        dust_bias *= 0.10
                    if random.random() < dust_bias:
                        energy[cell_index] = max(energy[cell_index], 0.16 + (core_ratio * 0.22))
                        glyph[cell_index]  = random.choice(profile["dust_chars"])

                if not in_core_guard and energy[cell_index] < 0.14 and random.random() < (0.08 if upper else 0.14):
                    glyph[cell_index]  = random.choice(profile["dust_chars"])
                    energy[cell_index] = max(energy[cell_index], 0.12)

            field["last_tick"] = frame_tick
            return energy, glyph

        def build_side(field_key: str, *, frame_tick: int, upper: bool, activity: float) -> Text:
            profile = side_profiles[field_key]
            energy, glyph = refresh_field(
                field_key, frame_tick=frame_tick, activity=activity, upper=upper
            )

            part = Text()
            for cell_index, cell_level in enumerate(energy):
                core_distance = (side_width - 1 - cell_index) if profile["anchor"] >= 0.5 else cell_index
                in_core_guard = core_distance < profile["core_guard"]

                if in_core_guard and cell_level < (0.34 if upper else 0.28):
                    part.append(" ", style=f"bold {theme['noise_dim']}")
                    continue
                if cell_level < 0.10:
                    part.append(" ", style=f"bold {theme['noise_dim']}")
                    continue
                core_ratio = 1.0 - (core_distance / max(1, side_width - 1))
                glow_level = max(0.0, min(1.0, cell_level * (profile["glow_bias"] + (core_ratio * 0.12))))

                char  = glyph[cell_index] if glyph[cell_index] != " " else random.choice(profile["dust_chars"])
                color = mix_hex_color(theme["noise_dim"], theme["noise_live"], glow_level)
                part.append(char, style=f"bold {color}")
            return part

        def build_scanline(activity: float, *, frame_tick: int, upper: bool) -> Text:
            left_name  = "upper_left" if upper else "lower_left"
            right_name = "upper_right" if upper else "lower_right"

            left  = build_side(left_name, frame_tick=frame_tick, upper=upper, activity=activity)
            right = build_side(right_name, frame_tick=frame_tick + 1, upper=upper, activity=activity)

            line = Text()
            line.append_text(left)
            aperture_width = max(
                0, core_width + (aperture_offsets["upper"] if upper else aperture_offsets["lower"])
            )
            line.append(" " * aperture_width, style=f"bold {theme['noise_dim']}")
            line.append_text(right)
            return center_text(line, pad_style=f"bold {theme['noise_dim']}")

        def build_title_line(
            visible: int,
            *,
            glow: float,
            glitch_map: dict[int, str] | None = None
        ) -> Text:
            title_color = mix_hex_color(theme["title_dim"], theme["title_live"], max(0.0, min(1.0, glow)))
            chars: list[tuple[str, str]] = []

            glitch_map = glitch_map or {}
            for char_index, char_value in enumerate(title):
                if char_index < visible:
                    rendered = char_value
                    color    = title_color

                    glitch_char = glitch_map.get(char_index, "")
                    if glitch_char:
                        rendered = glitch_char
                        color    = theme["glitch_b"] if char_index == max(glitch_map) else theme["glitch_a"]
                    chars.append((rendered, color))
                else:
                    chars.append((" ", theme["title_dim"]))

            inner = Text()
            for rendered, color in chars:
                inner.append(rendered, style=f"bold {color}")
            return center_text(inner)

        def block(top: Text, middle: Text, bottom: Text) -> Text:
            out = Text("\n")
            out.append_text(top)
            out.append("\n")
            out.append_text(middle)
            out.append("\n")
            out.append_text(bottom)
            out.append("\n")
            return out

        def build_frame(
            *,
            upper_band: float,
            lower_band: float,
            title_visible: int,
            title_glow: float,
            tick_cursor: int,
            title_glitch: dict[int, str] | None = None,
        ) -> Text:
            return block(
                build_scanline(upper_band, frame_tick=tick_cursor, upper=True),
                build_title_line(title_visible, glow=title_glow, glitch_map=title_glitch),
                build_scanline(lower_band, frame_tick=tick_cursor + 2, upper=False),
            )

        last_frame = block(
            build_scanline(0.16, frame_tick=0, upper=True),
            build_title_line(0, glow=0.0),
            build_scanline(0.12, frame_tick=3, upper=False),
        )

        with Live(last_frame, console=Design.console, refresh_per_second=30, transient=True) as live:
            current_tick = 0

            for top_intensity, bottom_intensity, pause in (
                (0.22, 0.16, 0.022),
                (0.36, 0.24, 0.026),
                (0.54, 0.34, 0.028),
            ):
                last_frame = build_frame(
                    upper_band=top_intensity,
                    lower_band=bottom_intensity,
                    title_visible=0,
                    title_glow=top_intensity * 0.35,
                    tick_cursor=current_tick,
                )
                live.update(last_frame)
                time.sleep(pause)
                current_tick += 1

            title_length = len(title)

            for typed_count in range(1, title_length + 1):
                glitch_overrides = {typed_count - 1: random.choice(glitch_chars)}
                if typed_count >= 2:
                    glitch_overrides[typed_count - 2] = random.choice((".", ":", "¦"))
                last_frame = build_frame(
                    upper_band=0.62,
                    lower_band=0.52,
                    title_visible=typed_count,
                    title_glow=0.28 + (typed_count / max(1, title_length)) * 0.58,
                    tick_cursor=current_tick,
                    title_glitch=glitch_overrides,
                )
                live.update(last_frame)
                time.sleep(0.018)
                current_tick += 1

                last_frame = build_frame(
                    upper_band=0.54,
                    lower_band=0.44,
                    title_visible=typed_count,
                    title_glow=0.34 + (typed_count / max(1, title_length)) * 0.66,
                    tick_cursor=current_tick,
                )
                live.update(last_frame)
                time.sleep(0.024)
                current_tick += 1

            for glitch_overrides, top_intensity, bottom_intensity, pause in (
                ({title_length - 1: random.choice(glitch_chars)}, 0.46, 0.36, 0.030),
                ({max(0, title_length - 2): random.choice(glitch_chars)}, 0.34, 0.24, 0.038),
                ({}, 0.18, 0.12, 0.050),
            ):
                last_frame = build_frame(
                    upper_band=top_intensity,
                    lower_band=bottom_intensity,
                    title_visible=title_length,
                    title_glow=1.0,
                    tick_cursor=current_tick,
                    title_glitch=glitch_overrides,
                )
                live.update(last_frame)
                time.sleep(pause)
                current_tick += 1

            for top_intensity, bottom_intensity, glow_ratio, pause in (
                (0.12, 0.08, 0.62, 0.050),
                (0.05, 0.03, 0.28, 0.078),
            ):
                last_frame = build_frame(
                    upper_band=top_intensity,
                    lower_band=bottom_intensity,
                    title_visible=title_length,
                    title_glow=glow_ratio,
                    tick_cursor=current_tick,
                )
                live.update(last_frame)
                time.sleep(pause)
                current_tick += 1

        Design.console.print(last_frame)

    @staticmethod
    def show_done() -> None:
        task_done = textwrap.dedent(f"""\
            [bold #00FF88]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Done             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_done)

    @staticmethod
    def show_exit() -> None:
        task_exit = textwrap.dedent(f"""\
            [bold #FFEE55]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Exit             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_exit)

    @staticmethod
    def show_fail() -> None:
        task_fail = textwrap.dedent(f"""\
            [bold #FF4444]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Fail             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_fail)

    @staticmethod
    def notify_update(local: dict[str, typing.Any], remote: dict[str, typing.Any]) -> None:
        Design.console.print(
            f"\n[bold]╭────── update available ──────╮\n"
            f"current:  [bold #AFFFFF]{local.get('version') or '-'}[/]\n"
            f"latest :  [bold #AFFFFF]{remote.get('version') or '-'}[/]\n"
            f"notes  :  [bold #8A8A8A]{remote.get('notes') or '-'}[/]\n"
        )

    @staticmethod
    async def typewriter(
        live: Live,
        delta: str,
        out: str,
        begin_delay: float,
        final_delay: float,
        cursor: str,
        *,
        max_lines: int | None = None,
        renderer: typing.Optional[typing.Callable[[str, str], Text]] = None
    ) -> tuple[str, float]:
        """
        - 默认：（整段 out 渲染）
        - 若 max_lines 指定：只显示末尾 max_lines 行（固定高度窗口）
        - renderer(text, cursor) 可自定义渲染（不传则使用默认 Text(text)+cursor）
        """

        pause: float         = 0.025   # 0.06 -> 0.025
        jitter: float        = 0.0012  # 0.0025 -> 0.0012
        breathe_every: int   = 160     # 80 -> 160（更少触发呼吸）
        breathe_pause: float = 0.06    # 0.18 -> 0.06
        glitch: float        = 0.003   # 0.01 -> 0.003（更少闪）
        flush_chars: int     = 2       # 3 -> 2（更跟手）
        flush_ms: float      = 0.012   # 0.02 -> 0.012

        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        n = max(len(delta), 1)
        pending, last_flush = 0, loop.time()

        def default_renderer(text: str, cur_cursor: str) -> Text:
            t = Text(text, style="bold", no_wrap=bool(max_lines), overflow="crop")
            t.append(cur_cursor, style="bold")
            return t

        render_fn = renderer or default_renderer

        class Window(object):

            __slots__ = ("lines", "cur")

            def __init__(self, max_len: int):
                self.lines: deque[str] = deque(maxlen=max_len)
                self.cur: str = ""

            def append_char(self, char: str) -> None:
                if char == "\n":
                    self.lines.append(self.cur)
                    self.cur = ""
                else:
                    self.cur += char

            def set_from_out_tail(self, full_out: str) -> None:
                parts = full_out.split("\n")  # 保留末尾是否换行的信息

                if full_out.endswith("\n"):
                    cur = ""
                    lines = parts[:-1]  # 最后一个是空串
                else:
                    cur = parts[-1] if parts else ""
                    lines = parts[:-1]

                self.lines.clear()
                for ln in lines[-self.lines.maxlen:]:
                    self.lines.append(ln)

                self.cur = cur

            def text(self, *, pad_to: typing.Optional[int] = None) -> str:
                rows = list(self.lines) + [self.cur]  # 始终带 cur 行（可为空串）
                if pad_to and pad_to > 0 and len(rows) < pad_to:
                    rows = [""] * (pad_to - len(rows)) + rows
                return "\n".join(rows)

        win: typing.Optional[Window] = Window(max_lines) if max_lines else None
        use_window = win is not None

        def render() -> None:
            text = win.text() if use_window else out
            live.update(render_fn(text, cursor))

        # 初始化窗口：如果 out 已有内容（跨多次 delta），只取末尾 max_lines 行
        if use_window and out:
            win.set_from_out_tail(out)
            render()

        for i, ch in enumerate(delta):
            # 线性加速 + 抖动
            denominator = (n - 1) if n > 1 else 1
            d = begin_delay + (final_delay - begin_delay) * (i / denominator)
            d = max(0.0, d + random.uniform(-jitter, jitter))

            # 标点分级停顿
            if ch in "，,": d += pause * 0.6
            elif ch in "。.!！?？": d += pause * 1.4
            elif ch in "；;：:": d += pause

            # 偶发 glitch
            if glitch and ch not in "\n\r\t" and ch.strip() and random.random() < glitch:
                if use_window:
                    live.update(
                        Text(win.text(), style="bold")
                        + Text(random.choice("▓▒░"), style="bold")
                        + Text(cursor, style="bold")
                    )
                    await asyncio.sleep(0.010)
                    render()
                else:
                    live.update(Text(out + random.choice("▓▒░") + cursor))
                    await asyncio.sleep(0.010)
                    render()

            # 追加字符（out 用于返回/持久化；窗口用于展示）
            out += ch
            if use_window:
                win.append_char(ch)

            pending += 1
            now = loop.time()
            if pending >= flush_chars or (now - last_flush) >= flush_ms:
                render()
                last_flush, pending = now, 0

            # breathe
            if breathe_every and (len(out) % breathe_every == 0):
                d += breathe_pause

            await asyncio.sleep(d)

        if pending:
            render()

        return out, final_delay

    @staticmethod
    async def cursor_blink(
        live: Live,
        out: str,
        cursor: str,
        *,
        max_lines: typing.Optional[int] = None,
        renderer: typing.Optional[typing.Callable[[str, bool], Text]] = None
    ) -> None:
        """
        - 默认：用全量 out
        - max_lines：只展示末尾 max_lines 行（与 typewriter 窗口一致）
        - renderer(text, cursor_on)：自定义渲染
        """

        def tail(text: str) -> str:
            if not max_lines: return text
            parts = text.splitlines()
            return "\n".join(parts[-max_lines:])

        def default_render(text: str, cursor_on: bool) -> Text:
            base = Text(text, style="bold", no_wrap=bool(max_lines), overflow="crop")
            if cursor_on:
                base += Text(cursor, style="reverse")
            return base

        r = renderer or default_render
        view = tail(out)

        for _ in range(2):
            live.update(r(view, True))
            await asyncio.sleep(0.08)
            live.update(r(view, False))
            await asyncio.sleep(0.06)

        live.update(r(view, False))

    @staticmethod
    def build_file_tree(file_path: str) -> None:
        """
        显示树状图。
        """
        color_schemes = {
            "Ocean Breeze": ["#AFD7FF", "#87D7FF", "#5FAFD7"],  # 根 / 中间 / 文件
            "Forest Pulse": ["#A8FFB0", "#87D75F", "#5FAF5F"],
            "Neon Sunset": ["#FFAF87", "#FF875F", "#D75F5F"],
            "Midnight Ice": ["#C6D7FF", "#AFAFD7", "#8787AF"],
            "Cyber Mint": ["#AFFFFF", "#87FFFF", "#5FD7D7"]
        }
        file_icons = {
            "folder": "📁",
            ".json": "📦",
            ".yaml": "🧾",
            ".yml": "🧾",
            ".md": "📝",
            ".log": "📄",
            ".html": "🌐",
            ".sh": "🔧",
            ".bat": "🔧",
            ".db": "🗃️",
            ".sqlite": "🗃️",
            ".zip": "📦",
            ".tar": "📦",
            "default": "📄"
        }
        text_color = random.choice([
            "#8A8A8A", "#949494", "#9E9E9E", "#A8A8A8", "#B2B2B2"
        ])

        root_color, folder_color, file_color = random.choice(list(color_schemes.values()))

        choice_icon: typing.Callable[
            [typing.Union[Path, str]], str
        ] = lambda x: file_icons["folder"] if (y := Path(x)).is_dir() else (
            file_icons[n] if (n := y.name.lower()) in file_icons else file_icons["default"]
        )

        parts = Path(file_path).parts

        # 根节点
        root = parts[0]
        tree = Tree(
            f"[bold {text_color}]{choice_icon(root)} {root}[/]", guide_style=f"bold {root_color}"
        )
        current_path = parts[0]
        current_node = tree

        # 处理中间的文件夹
        for part in parts[1:-1]:
            current_path = Path(current_path, part)
            current_node = current_node.add(
                f"[bold {text_color}]{choice_icon(current_path)} {part}[/]", guide_style=f"bold {folder_color}"
            )

        ext = (file := Path(parts[-1])).suffix.lower()
        current_node.add(f"[bold {file_color}]{choice_icon(ext)} {file.name}[/]")

        Design.console.print(tree)

    @staticmethod
    async def compile_animation(duration: float = 2, fps: int = 10) -> None:
        line_w = min(72, max(52, Design.console.width - 4))
        bar_w  = 28

        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        bits = "01"
        rng  = random.Random(7)
        logo = const.APP_DESC

        def shimmer_logo(t: int) -> Text:
            pos = t % max(1, len(logo))
            out = Text()
            for i, ch in enumerate(logo):
                d = abs(i - pos)
                if d == 0:
                    out.append(ch, style="bold #87FFFF")
                elif d == 1:
                    out.append(ch, style="bold #AFFFFF")
                elif d == 2:
                    out.append(ch, style="bold #5FFFFF")
                else:
                    out.append(ch, style="bold #BBBBBB")
            return out

        def glitch_line(t: int) -> Text:
            core = f"boot sequence: stage {1 + (t // 8) % 3}/3"
            core = core.ljust(line_w)
            out  = Text()
            if t % 10 in (0, 1):
                s = list(core)
                for _ in range(3):
                    idx = rng.randrange(0, min(line_w, len(s)))
                    s[idx] = rng.choice("≈≋∿-_/\\")
                out.append("".join(s), style="dim #AAAAAA")
            else:
                out.append(core, style="dim #8A8A8A")
            return out

        def progress_bar(p: float, t: int) -> Text:
            filled = max(0, min(bar_w, int(bar_w * p)))
            scan   = int((bar_w - 1) * (0.5 + 0.5 * (1 if (t // 6) % 2 == 0 else -1)))

            out = Text()
            out.append(f"{spin[t % len(spin)]} ", style="bold #AFFFFF")

            out.append_text(shimmer_logo(t))
            out.append(" ", style="bold")

            out.append("[", style="bold #888888")
            for i in range(bar_w):
                if i < filled:
                    if i == (filled - 1) or i == scan:
                        out.append("█", style="bold #87FFFF")
                    else:
                        out.append("█", style="bold #5FFFFF")
                else:
                    out.append("·", style="bold #3A3A3A")
            out.append("]", style="bold #888888")

            pct = f" {int(p * 100):>3d}%"
            out.append(pct, style="bold #87FFAF")

            if (plain_len := len(out.plain)) < line_w:
                out.append(" " * (line_w - plain_len))

            return out

        def micro_logs(t: int) -> Text:
            stage = (t // 10) % 3
            a = ["• preparing…", "• compiling…", "• packaging…"][stage].ljust(line_w)
            b = ["• warming cache…", "• linking…", "• sealing artifacts…"][stage].ljust(line_w)
            out = Text()
            out.append(a + "\n", style="bold dim")
            out.append(b, style="bold dim")
            return out

        def bit_rain(t: int) -> Text:
            rng2 = random.Random(t)
            s    = "".join(rng2.choice(bits + "  ") for _ in range(line_w))
            out  = Text()

            out.append(s, style="bold #2A2A2A")
            return out

        def frame(t: int, p: float) -> Text:
            txt = Text()
            txt.append_text(progress_bar(p, t))
            txt.append("\n")
            txt.append_text(glitch_line(t))
            txt.append("\n")
            txt.append_text(micro_logs(t))
            txt.append("\n")
            txt.append_text(bit_rain(t))
            return txt

        ticks = max(1, int(duration * fps))
        with Live(frame(0, 0.0), console=Design.console, refresh_per_second=fps) as live:
            for tick in range(ticks):
                phase = (tick + 1) / ticks
                live.update(frame(tick, phase))
                await asyncio.sleep(1 / fps)

            done = Text()
            done.append("✓ build ready".ljust(line_w), style="bold #87FF00")
            done.append("\n")
            done.append("• prepared ✓".ljust(line_w), style="bold dim")
            done.append("\n")
            done.append("• compiled ✓".ljust(line_w), style="bold dim")
            done.append("\n")
            done.append("• packaged ✓".ljust(line_w), style="bold dim")
            done.append("\n")
            done.append((" " * line_w), style="bold #2A2A2A")
            live.update(done)
            await asyncio.sleep(0.25)

    @staticmethod
    async def particle_aggregate() -> None:

        def padding(char: str) -> str:
            return indent + char[:w].ljust(w)

        def compose(text: str, font_dict: dict[str, list[str]], gap: int = 2) -> list[str]:
            text  = text.upper()
            blank = [" " * 7] * 7
            parts = [font_dict.get(ch, blank) for ch in text]
            sep   = " " * gap
            return [sep.join(part[r] for part in parts) for r in range(7)]

        def backcloth() -> list[list[str]]:
            # 背景粒子：（中心稀疏、边缘更密）
            dots = ["·", "∙", "⋅", "•", "∘"]
            base = 0.06  # 基础密度（中心区域）
            edge = 0.06  # 边缘加密（越大越“包围感”）

            cx, cy = (w - 1) / 2, (h - 1) / 2
            max_d = (cx * cx + cy * cy) ** 0.5 or 1.0

            backcloth_list: list[list[str]] = []
            for y_ in range(h):
                row: list[str] = []
                for x_ in range(w):
                    d_x, d_y = x_ - cx, y_ - cy
                    d = ((d_x * d_x + d_y * d_y) ** 0.5) / max_d  # 0=中心, 1=边缘
                    p = base + edge * d
                    row.append(rng.choice(dots) if rng.random() < p else " ")
                backcloth_list.append(row)

            return backcloth_list

        font = {
            "M": ["##   ##", "### ###", "## # ##", "##   ##", "##   ##", "##   ##", "##   ##"],
            "I": ["#######", "  ###  ", "  ###  ", "  ###  ", "  ###  ", "  ###  ", "#######"],
            "N": ["##   ##", "###  ##", "#### ##", "## ####", "##  ###", "##   ##", "##   ##"],
            "D": ["###### ", "##   ##", "##   ##", "##   ##", "##   ##", "##   ##", "###### "],
        }

        # (settled, moving, boot_line, ready_line, bg)  —— 每套同色系
        palettes = [
            # Cyan 系（品牌感强）
            ("bold #5FFFFF", "bold #87FFFF", "dim  #3FBDBD", "bold #5FFFFF", "bold #102A2A"),  # cyan neon
            ("bold #00D7FF", "bold #5FFFFF", "dim  #2FA9BF", "bold #00D7FF", "bold #0F2328"),  # deep cyan

            # Aqua / Teal 系（更冷更科技）
            ("bold #5FFFD7", "bold #87FFE9", "dim  #3FBFAE", "bold #5FFFD7", "bold #102622"),  # aqua mint
            ("bold #00FFAF", "bold #5FFFC7", "dim  #35BFA0", "bold #00FFAF", "bold #10261F"),  # teal green

            # Green 系（偏“完成/OK”）
            ("bold #87FF00", "bold #AFFF5F", "dim  #6BBF2A", "bold #87FF00", "bold #17230F"),  # lime
            ("bold #5FFF87", "bold #87FFAF", "dim  #4ABF73", "bold #5FFF87", "bold #112016"),  # spring green

            # Yellow / Gold 系（偏暖但仍统一）
            ("bold #FFD75F", "bold #FFEA87", "dim  #BFA24A", "bold #FFD75F", "bold #241F12"),  # warm gold
            ("bold #FFAF5F", "bold #FFC287", "dim  #BF824A", "bold #FFAF5F", "bold #241A12"),  # amber

            # Purple 系（霓虹紫科技）
            ("bold #AF87FF", "bold #C7AFFF", "dim  #8A66CF", "bold #AF87FF", "bold #1C1426"),  # violet
            ("bold #D787FF", "bold #E7B7FF", "dim  #A066CF", "bold #D787FF", "bold #201126"),  # magenta purple

            # Pink 系（少量跳色也可，但主题内仍统一）
            ("bold #FF5FAF", "bold #FF87C7", "dim  #C74F8A", "bold #FF5FAF", "bold #24121C"),  # neon pink
        ]

        duration: float = 1.0
        fps: int        = 30
        indent: str     = "  "

        rng        = random.Random()
        candidates = [const.APP_NAME.upper()]
        word       = rng.choice(candidates)

        style_settled, style_moving, style_begin, style_final, style_bg = rng.choice(palettes)

        glyph = compose(word, font)

        gw, gh       = len(glyph[0]), len(glyph)
        pad_x, pad_y = 0, 1
        ox, oy       = pad_x, pad_y
        w, h         = gw + pad_x * 2, gh + pad_y * 2

        targets: list[tuple[int, int]] = [
            (ox + c, oy + r) for r in range(gh) for c in range(gw) if glyph[r][c] == "#"
        ]
        target_set = set(targets)

        # 背景粒子：（中心稀疏、边缘更密）
        background = backcloth()

        particles: list[Design.Particle] = []
        for (tx, ty) in targets:
            if (side := rng.randrange(4)) == 0:
                # top
                x, y = rng.uniform(0, w - 1), -rng.uniform(1, h)
            elif side == 1:
                # bottom
                x, y = rng.uniform(0, w - 1), h - 1 + rng.uniform(1, h)
            elif side == 2:
                # left
                x, y = -rng.uniform(1, w), rng.uniform(0, h - 1)
            else:
                # right
                x, y = w - 1 + rng.uniform(1, w), rng.uniform(0, h - 1)
            particles.append(Design.Particle(x=x, y=y, tx=tx, ty=ty))

        ticks      = max(1, int(duration * fps))
        slogan     = f"Agent ready • launch"
        begin_line = padding(f"Booting… [{word}]")
        final_line = padding(f"✓ {slogan} [{word}]\n")

        def render(t: int, final: bool = False) -> Text:
            grid_state: dict[tuple[int, int], bool] = {}
            percent = min(1.0, (t + (1 if final else 0)) / ticks)

            for ptc in particles:
                if 0 <= (cx := int(round(ptc.x))) < w and 0 <= (cy := int(round(ptc.y))) < h:
                    d = abs(ptc.x - ptc.tx) + abs(ptc.y - ptc.ty)
                    settled = final or (percent > 0.85 and d < 0.9)

                    if (cx, cy) not in grid_state:
                        grid_state[(cx, cy)] = settled
                    else:
                        grid_state[(cx, cy)] = grid_state[(cx, cy)] or settled

            out: Text = Text()
            for y_loc in range(h):
                line_chars, line_styles = [], []
                for x_loc in range(w):
                    if (key := (x_loc, y_loc)) in grid_state:
                        if grid_state[key]:
                            ch, style = "█", style_settled
                        else:
                            ch, style = "•", style_moving
                    else:
                        if final and (x_loc, y_loc) in target_set:
                            ch, style = "█", style_settled
                        else:
                            ch, style = background[y_loc][x_loc], style_bg

                    line_chars.append(ch)
                    line_styles.append(style)

                out.append(indent)

                i = 0
                while i < w:
                    j = i + 1
                    while j < w and line_styles[j] == line_styles[i]:
                        j += 1
                    out.append("".join(line_chars[i:j]), style=line_styles[i])
                    i = j

                if y_loc != h - 1: out.append("\n")

            out.append("\n")
            if final:
                out.append(final_line, style=style_final)
            else:
                out.append(begin_line, style=style_begin)

            return out

        with Live(render(0), console=Design.console, refresh_per_second=fps) as live:
            for tick in range(ticks):
                phase  = (tick + 1) / ticks
                ease   = 1 - (1 - phase) * (1 - phase)
                alpha  = 0.14 + 0.10 * ease
                jitter = 1.4 * (1 - ease)

                for particle in particles:
                    dx = particle.tx - particle.x
                    dy = particle.ty - particle.y
                    particle.x += dx * alpha + (rng.random() - 0.5) * jitter * 0.18
                    particle.y += dy * alpha + (rng.random() - 0.5) * jitter * 0.10

                live.update(render(tick))
                await asyncio.sleep(1 / fps)

            live.update(render(ticks, final=True))

    @staticmethod
    async def download_animation(state: dict[str, typing.Any], stop_event: asyncio.Event) -> None:
        """下载动画"""

        def fmt_size(num: float) -> str:
            unit = ["B", "KB", "MB", "GB", "TB"]
            idx  = 0
            n    = float(max(0.0, num))

            while n >= 1024.0 and idx < len(unit) - 1:
                n /= 1024.0
                idx += 1

            if n >= 100:
                return f"{n:>3.0f}{unit[idx]:<2}"
            if n >= 10:
                return f"{n:>4.1f}{unit[idx]:<2}"
            return f"{n:>4.2f}{unit[idx]:<2}"

        async def frames() -> typing.AsyncGenerator[Text, None]:
            tick = 0
            while not stop_event.is_set():
                tick += 1

                stage    = str(state.get("stage") or "warming")
                filename = str(state.get("filename") or "package")
                phase    = float(state.get("phase") or 0.0)
                done     = int(state.get("done") or 0)

                if phase <= 0.0:
                    phase = min(0.08, phase + 0.01)
                    state["phase"] = phase

                bar_w  = 24
                cursor = int((bar_w - 1) * (0.5 + 0.5 * math.sin(tick * 0.22)))
                fill   = int(bar_w * max(0.0, min(1.0, phase)))

                chars: list[str] = []
                for i in range(bar_w):
                    if i < fill: chars.append("█")
                    elif i == cursor: chars.append("▓")
                    elif abs(i - cursor) == 1: chars.append("▒")
                    else: chars.append("·")

                done_s = fmt_size(done)

                line1 = f"{spin[tick % len(spin)]} {stage}"
                line2 = filename
                line3 = f"[{' '.join(chars)}] {done_s}"

                out = Text()
                out.append(line1[:width].ljust(width), style="bold #AFFFFF")
                out.append("\n")
                out.append(line2[:width].ljust(width), style="bold dim #8A8A8A")
                out.append("\n")
                out.append(line3[:width].ljust(width), style="bold dim #8A8A8A")

                yield out
                await asyncio.sleep(1 / 18)

            for step in range(3):
                filename = str(state.get("filename") or "package")
                done_s   = fmt_size(float(state.get("done") or 0))

                out = Text()
                out.append("✓ complete".ljust(width), style="bold #87FFAF")
                out.append("\n")
                out.append(filename[:width].ljust(width), style="bold dim #8A8A8A")
                out.append("\n")
                out.append(f"size {done_s}".ljust(width), style="bold dim #8A8A8A")

                yield out
                await asyncio.sleep(0.06)

        width = 56
        spin  = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        agen  = frames()
        first = await agen.__anext__()

        with Live(first, console=Design.console, refresh_per_second=18) as live:
            async for frame in agen:
                live.update(frame)

    async def agent_wait_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], tuple[str, str]]
    ) -> None:
        """订阅模式呼吸等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        fps = 30
        width = min(34, max(24, self.console.width - 22))
        text_width = min(56, max(28, self.console.width - 10))
        base_pad = "  "
        colors = {
            "shell"      : "#223444",
            "shell_dim"  : "#13202C",
            "core"       : "#F2FFFD",
            "near"       : "#8EDAE0",
            "beam"       : "#73C7D4",
            "beam_dim"   : "#35586A",
            "title"      : "#E6FBF3",
            "detail"     : "#94A6BA",
            "detail_dim" : "#60758C",
            "pulse"      : "#9BDCF2",
            "pulse_dim"  : "#4B6B86",
        }

        def fit(text: str) -> str:
            raw = (text or "").strip()
            if cell_len(raw) <= text_width:
                return raw
            trimmed = raw
            while trimmed and cell_len(trimmed + "…") > text_width:
                trimmed = trimmed[:-1]
            return trimmed + "…"

        def prefix_text(symbol: str, style: str) -> Text:
            out = Text()
            out.append(symbol, style=style)
            out.append(" " * max(1, 3 - cell_len(symbol)))
            return out

        def render(frame_idx: int) -> Text:
            title, detail = snapshot()
            phase = frame_idx / fps
            breathe = 0.5 + 0.5 * math.sin(phase * 1.85)
            left_breathe = 0.5 + 0.5 * math.sin(phase * 1.85 - 0.9)
            right_breathe = 0.5 + 0.5 * math.sin(phase * 1.85 + 0.9)
            sweep = 0.5 + 0.5 * (
                0.58 * math.sin(phase * 2.1)
                + 0.28 * math.sin(phase * 1.17 + 1.1)
                + 0.14 * math.cos(phase * 0.63 + 2.0)
            )
            title_glow = 0.48 + breathe * 0.24
            detail_glow = 0.18 + breathe * 0.18
            pulse_glow = 0.30 + breathe * 0.30
            title_tone = ease_in_out_sine(title_glow)
            detail_tone = ease_in_out_sine(detail_glow)
            pulse_tone = ease_in_out_sine(pulse_glow)
            pulse = "◦" if math.sin(phase * 2.0) > 0 else "◌"
            spin = "•" if math.sin(phase * 2.6) > 0 else "·"

            chars = [" "] * width
            styles: dict[int, str] = {}
            left = 2
            center = width // 2
            right = width - 3
            trail_left = left + 2
            trail_right = right - 2
            head = trail_left + int((trail_right - trail_left) * sweep)

            chars[left] = "◌"
            styles[left] = f"bold {mix_hex_color(colors['shell_dim'], colors['near'], 0.18 + left_breathe * 0.42)}"
            chars[center] = "◎" if breathe > 0.55 else "◉"
            styles[center] = f"bold {mix_hex_color(colors['near'], colors['core'], 0.55 + breathe * 0.45)}"
            chars[right] = "◌"
            styles[right] = f"bold {mix_hex_color(colors['shell_dim'], colors['near'], 0.18 + right_breathe * 0.42)}"

            for pos in range(left + 2, right - 1):
                if abs(pos - head) == 0:
                    chars[pos] = "•"
                    styles[pos] = f"bold {colors['core']}"
                elif 0 < head - pos <= 3 or 0 < pos - head <= 3:
                    chars[pos] = "·"
                    styles[pos] = f"bold {colors['beam']}"
                elif abs(pos - head) <= 6 and (pos + frame_idx) % 2 == 0:
                    chars[pos] = "·"
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif (pos + frame_idx) % 9 == 0 and trail_left <= pos <= trail_right:
                    chars[pos] = "·"
                    styles[pos] = f"bold {colors['beam_dim']}"

            line1 = Text()
            line1.append(base_pad)
            for idx, char in enumerate(chars):
                line1.append(char, style=styles.get(idx, ""))

            line2 = Text()
            line2.append(base_pad)
            line2.append_text(
                prefix_text(
                    pulse,
                    f"bold {mix_hex_color(colors['pulse_dim'], colors['pulse'], pulse_tone)}"
                )
            )
            line2.append(
                fit(title or "Subscription Idle"),
                style=f"bold {mix_hex_color(colors['detail'], colors['title'], title_tone)}"
            )

            line3 = Text()
            line3.append(base_pad)
            line3.append_text(
                prefix_text(
                    spin,
                    f"bold {mix_hex_color(colors['shell_dim'], colors['pulse_dim'], detail_tone)}"
                )
            )
            line3.append(
                fit(detail or "Waiting for link state"),
                style=f"{mix_hex_color(colors['detail_dim'], colors['detail'], detail_tone)}"
            )

            out = Text(no_wrap=True, overflow="crop")
            out.append_text(line1)
            out.append("\n")
            out.append_text(line2)
            out.append("\n")
            out.append_text(line3)
            return out

        tick = 0
        with Live(render(0), console=Design.console, refresh_per_second=fps, transient=True) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(render(tick))
                await asyncio.sleep(1 / fps)

    async def agent_connect_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], tuple[str, str]]
    ) -> None:
        """订阅模式建连等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        fps = 24
        width = min(34, max(24, self.console.width - 22))
        text_width = min(56, max(28, self.console.width - 10))
        base_pad = "  "
        colors = {
            "pulse"      : "#C3E8FF",
            "pulse_dim"  : "#6B89A3",
            "title"      : "#F7FBFF",
            "detail"     : "#A8B6C8",
            "detail_dim" : "#6E8095",
            "node"       : "#7DD3FC",
            "node_hot"   : "#F0FBFF",
            "rail_dim"   : "#284055",
            "rail_mid"   : "#4FA9D4",
            "rail_hot"   : "#D9F7FF",
        }

        def fit(text: str) -> str:
            raw = (text or "").strip()
            if cell_len(raw) <= text_width:
                return raw
            trimmed = raw
            while trimmed and cell_len(trimmed + "…") > text_width:
                trimmed = trimmed[:-1]
            return trimmed + "…"

        def prefix_text(symbol: str, style: str) -> Text:
            out = Text()
            out.append(symbol, style=style)
            out.append(" " * max(1, 3 - cell_len(symbol)))
            return out

        def render(frame_idx: int) -> Text:
            title, detail = snapshot()
            phase = frame_idx / fps
            breathe = 0.5 + 0.5 * math.sin(phase * 2.2)
            scan = 0.5 + 0.5 * math.sin(phase * 4.2)
            title_glow = 0.58 + breathe * 0.18
            detail_glow = 0.24 + breathe * 0.12
            pulse_glow = 0.42 + breathe * 0.20
            title_tone = ease_in_out_sine(title_glow)
            detail_tone = ease_in_out_sine(detail_glow)
            pulse_tone = ease_in_out_sine(pulse_glow)

            chars = [" "] * width
            styles: dict[int, str] = {}
            left = 1
            center = width // 2
            right = width - 2
            left_lane = list(range(left + 1, center))
            right_lane = list(range(center + 1, right))
            head = min(len(left_lane) - 1, max(0, round(scan * (len(left_lane) - 1))))

            chars[left] = "◉"
            styles[left] = f"bold {mix_hex_color(colors['node'], colors['node_hot'], 0.35 + (0.5 + 0.5 * math.sin(phase * 2.2 - 0.8)) * 0.55)}"
            chars[center] = "◆" if breathe > 0.5 else "◈"
            styles[center] = f"bold {mix_hex_color(colors['node'], colors['node_hot'], 0.65 + breathe * 0.35)}"
            chars[right] = "◉"
            styles[right] = f"bold {mix_hex_color(colors['node'], colors['node_hot'], 0.35 + (0.5 + 0.5 * math.sin(phase * 2.2 + 0.8)) * 0.55)}"

            for idx, pos in enumerate(left_lane):
                distance = abs(idx - head)
                ratio = 0.22 if distance > 1 else (0.58 if distance == 1 else 1.0)
                chars[pos] = "═" if distance == 0 else "─"
                styles[pos] = f"bold {mix_hex_color(colors['rail_dim'], colors['rail_hot'], ratio)}"

            mirrored_head = len(right_lane) - 1 - head
            for idx, pos in enumerate(right_lane):
                distance = abs(idx - mirrored_head)
                ratio = 0.22 if distance > 1 else (0.58 if distance == 1 else 1.0)
                chars[pos] = "═" if distance == 0 else "─"
                styles[pos] = f"bold {mix_hex_color(colors['rail_dim'], colors['rail_hot'], ratio)}"

            line1 = Text()
            line1.append(base_pad)
            for idx, char in enumerate(chars):
                line1.append(char, style=styles.get(idx, ""))

            line2 = Text()
            line2.append(base_pad)
            line2.append_text(
                prefix_text(
                    "↺" if math.sin(phase * 2.8) > 0 else "↻",
                    f"bold {mix_hex_color(colors['pulse_dim'], colors['pulse'], pulse_tone)}"
                )
            )
            line2.append(
                fit(title or "Opening Fold Link"),
                style=f"bold {mix_hex_color(colors['detail'], colors['title'], title_tone)}"
            )

            line3 = Text()
            line3.append(base_pad)
            line3.append_text(
                prefix_text(
                    "·",
                    f"bold {mix_hex_color(colors['detail_dim'], colors['pulse_dim'], detail_tone)}"
                )
            )
            line3.append(
                fit(detail or "Waiting for subscription handshake"),
                style=f"{mix_hex_color(colors['detail_dim'], colors['detail'], detail_tone)}"
            )

            out = Text(no_wrap=True, overflow="crop")
            out.append_text(line1)
            out.append("\n")
            out.append_text(line2)
            out.append("\n")
            out.append_text(line3)
            return out

        tick = 0
        with Live(render(0), console=Design.console, refresh_per_second=fps, transient=True) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(render(tick))
                await asyncio.sleep(1 / fps)

    async def stream_wait_live(
        self,
        stop_event: asyncio.Event,
        theme: typing.Literal["chat", "fast", "plan"] = "chat"
    ) -> None:
        """流式等待动画效果。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        palettes: dict[str, dict[str, typing.Any]] = {
            "chat": {
                "glyphs": {
                    "spin"         : "◜◠◝◞◡◟",
                    "bubble_left"  : "〈《(",
                    "bubble_right" : ")》〉",
                    "bubble_dot"   : "●◉",
                    "focus"        : "◆",
                    "pulse"        : "•",
                    "echo"         : "·",
                    "beam_a"       : "═",
                    "beam_b"       : "─",
                    "noise"        : "˙",
                    "tail"         : "•",
                    "trail"        : "⋅",
                    "reply"        : "◦◎",
                    "listen"       : "◌◍",
                    "speak"        : "◉◍"
                },
                "colors": {
                    "prefix"     : "#A3E635",
                    "core"       : "#C4FFF0",
                    "near"       : "#9EF7E7",
                    "beam"       : "#67E8F9",
                    "beam_dim"   : "#3F9FB3",
                    "dust"       : "#3F3F46",
                    "sweep_core" : "#93C5FD",
                    "sweep_tail" : "#60A5FA",
                    "shell"      : "#244454",
                    "shell_dim"  : "#22313A",
                    "orbit_a"    : "#FDE68A",
                    "orbit_b"    : "#8BE9FD",
                    "orbit_c"    : "#5EEAD4"
                },
                "motion": {
                    "phase_div"      : 4.2,
                    "lead_freq"      : 1.08,
                    "reply_freq"     : 0.72,
                    "reply_phase"    : 1.45,
                    "breathe_freq"   : 0.88,
                    "sender_freq"    : 1.62,
                    "receiver_freq"  : 1.28,
                    "receiver_phase" : 2.2,
                    "chat_cycle"     : 16
                }
            },
            "fast": {
                "glyphs": {
                    "spin"   : "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
                    "packet" : "◈",
                    "core"   : "◆",
                    "near"   : "•",
                    "beam_a" : "=",
                    "beam_b" : "-",
                    "trail"  : ":",
                    "echo"   : "~",
                    "gate"   : ">",
                    "dust"   : "˙",
                    "orbit"  : ".",
                    "glitch" : "·:~"
                },
                "colors": {
                    "prefix"     : "#F59E0B",
                    "core"       : "#FFF3C4",
                    "near"       : "#FCD34D",
                    "beam"       : "#FB7185",
                    "beam_dim"   : "#BE5672",
                    "dust"       : "#4A2D33",
                    "sweep_core" : "#F97316",
                    "sweep_tail" : "#FB7185",
                    "shell"      : "#5B2C1A",
                    "shell_dim"  : "#3A2320",
                    "orbit_a"    : "#FDBA74",
                    "orbit_b"    : "#F472B6",
                    "orbit_c"    : "#FDE68A"
                },
                "motion": {
                    "phase_div"    : 3.0,
                    "lead_freq"    : 0.92,
                    "echo_phase"   : 0.85,
                    "pilot_freq"   : 1.8,
                    "pilot_offset" : 3.0,
                    "pilot_amp"    : 1.5
                }
            },
            "plan": {
                "glyphs": {
                    "spin"     : "◴◷◶◵",
                    "done"     : "◆",
                    "active"   : "◉",
                    "next"     : "◇",
                    "idle"     : "○",
                    "beam_a"   : "═",
                    "beam_b"   : "─",
                    "progress" : "▸",
                    "pulse"    : "•",
                    "echo"     : "·"
                },
                "colors": {
                    "prefix"     : "#34D399",
                    "core"       : "#D1FAE5",
                    "near"       : "#6EE7B7",
                    "beam"       : "#A7F3D0",
                    "beam_dim"   : "#4E9F8A",
                    "dust"       : "#31403D",
                    "sweep_core" : "#10B981",
                    "sweep_tail" : "#34D399",
                    "shell"      : "#1F4D45",
                    "shell_dim"  : "#203733",
                    "orbit_a"    : "#A7F3D0",
                    "orbit_b"    : "#93C5FD",
                    "orbit_c"    : "#C4B5FD"
                },
                "motion": {
                    "phase_div"   : 7.2,
                    "active_freq" : 0.8,
                    "bridge_freq" : 1.2
                }
            }
        }
        palette = palettes.get(theme, palettes["chat"])

        glyphs: dict[str, str] = palette["glyphs"]
        colors: dict[str, str] = palette["colors"]
        motion: dict[str, float] = palette["motion"]
        width = min(30, max(22, self.console.width - 22))

        fps  = 32
        spin = glyphs["spin"]
        tick = 0

        def clamp(pos: int) -> int:
            return max(0, min(width - 1, pos))

        def build_chat(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            cycle = max(12, int(motion["chat_cycle"]))
            beat  = i % cycle

            sender_talk     = beat in {1, 2, 3, 4, 5, 6, 7}
            sender_release  = beat in {8, 9}
            receiver_listen = beat in {10, 11, 12, 13}
            receiver_ack    = beat in {14, 15, 16, 17}

            left_rest      = 2
            right_rest     = width - 3
            sender_shift   = -1 if sender_talk else 0
            receiver_shift = 1 if receiver_ack else 0

            left       = clamp(left_rest + sender_shift)
            right      = clamp(right_rest + receiver_shift)
            lane_start = left + 2
            lane_end   = right - 2
            lane_span  = max(1, lane_end - lane_start)
            lead       = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head_ratio = 0.10 + 0.78 * lead

            if sender_talk:
                head_ratio = min(0.96, head_ratio + 0.06)

            head       = clamp(lane_start + int(lane_span * head_ratio))
            tail_len   = min(8, max(4, width // 4))
            reply_gate = 0.5 + 0.5 * math.sin((phase * motion["reply_freq"]) + motion["reply_phase"])
            reply_head = clamp(lane_end - int((lane_span * 0.22) * reply_gate))
            chars      = [" "] * width

            styles: dict[int, str] = dict()

            left_shell       = glyphs["bubble_left"][1 if sender_talk else (2 if sender_release else 0)]
            right_shell      = glyphs["bubble_right"][1 if receiver_ack else (2 if receiver_listen else 0)]
            left_dot_idle    = glyphs["bubble_dot"][0]
            left_dot_talk    = glyphs["speak"][0] if (i % 4) < 2 else glyphs["speak"][1]
            right_dot_idle   = glyphs["listen"][0]
            right_dot_listen = glyphs["listen"][1] if (i % 4) < 2 else glyphs["pulse"]
            reply_glyph      = glyphs["reply"][1] if (i % 4) < 2 else glyphs["reply"][0]

            chars[left]  = left_shell
            styles[left] = (
                f"bold {colors['core']}" if sender_talk
                else (f"bold {colors['near']}" if sender_release else f"bold {colors['orbit_a']}")
            )

            left_dot = clamp(left + 1)
            chars[left_dot] = left_dot_talk if sender_talk else (glyphs["tail"] if sender_release else left_dot_idle)
            styles[left_dot] = (
                f"bold {colors['sweep_core']}" if sender_talk
                else (f"bold {colors['sweep_tail']}" if sender_release else f"bold {colors['core']}")
            )

            left_aura = clamp(left + 2)
            if sender_talk:
                chars[left_aura] = glyphs["tail"] if (i % 4) in {1, 2} else glyphs["trail"]
                styles[left_aura] = f"bold {colors['sweep_tail']}"
            elif sender_release and (i % 2 == 0):
                chars[left_aura] = glyphs["trail"]
                styles[left_aura] = f"bold {colors['beam_dim']}"

            chars[right]  = right_shell
            styles[right] = (
                f"bold {colors['orbit_b']}" if receiver_ack
                else (f"bold {colors['near']}" if receiver_listen else f"bold {colors['orbit_c']}")
            )

            right_dot = clamp(right - 1)
            chars[right_dot] = reply_glyph if receiver_ack else (right_dot_listen if receiver_listen else right_dot_idle)
            styles[right_dot] = (
                f"bold {colors['orbit_b']}" if receiver_ack
                else (f"bold {colors['core']}" if receiver_listen else f"bold {colors['near']}")
            )

            right_aura = clamp(right - 2)
            if receiver_ack:
                chars[right_aura] = glyphs["pulse"] if (i % 4) in {1, 2} else reply_glyph
                styles[right_aura] = f"bold {colors['orbit_b']}"
            elif receiver_listen and (i % 2 == 0):
                chars[right_aura] = glyphs["listen"][0]
                styles[right_aura] = f"bold {colors['beam_dim']}"

            for pos in range(lane_start, lane_end + 1):
                dist = head - pos
                reply_dist = abs(pos - reply_head)

                if pos == head:
                    chars[pos] = glyphs["focus"]
                    styles[pos] = f"bold {colors['sweep_core']}"
                elif 0 < dist <= 2:
                    chars[pos] = glyphs["tail"]
                    styles[pos] = f"bold {colors['core']}"
                elif 0 < dist <= tail_len:
                    chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                    styles[pos] = f"bold {colors['beam']}"
                elif 0 < dist <= tail_len + 4:
                    if (pos + i) % 2 != 0:
                        continue
                    chars[pos] = glyphs["trail"]
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif head < pos <= min(lane_end, head + 2):
                    chars[pos] = glyphs["echo"]
                    styles[pos] = f"bold {colors['sweep_tail']}"
                elif pos >= right - 5 and reply_dist == 0 and receiver_ack:
                    chars[pos] = reply_glyph
                    styles[pos] = f"bold {colors['orbit_b']}"
                elif pos >= right - 5 and reply_dist <= 1 and receiver_ack and (i + pos) % 2 == 0:
                    chars[pos] = glyphs["pulse"]
                    styles[pos] = f"bold {colors['near']}"
                elif pos >= right - 6 and receiver_listen and (pos + i) % 2 == 0:
                    chars[pos] = glyphs["listen"][0]
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif pos >= right - 7 and (pos + i) % 3 == 0:
                    chars[pos] = glyphs["echo"]
                    styles[pos] = f"bold {colors['shell']}"
                elif pos <= left + 4 and sender_talk and (pos + i) % 2 == 0:
                    chars[pos] = glyphs["trail"] if (i + pos) % 4 == 0 else glyphs["pulse"]
                    styles[pos] = f"bold {colors['shell_dim']}"
                elif (pos + i) % 7 == 0:
                    chars[pos] = glyphs["noise"]
                    styles[pos] = f"bold {colors['dust']}"

            for pos in (clamp(left - 1), clamp(left - 2), clamp(right + 1), clamp(right + 2)):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["noise"]
                styles[pos] = f"bold {colors['shell_dim']}"

            if sender_talk:
                ember = clamp(left - 1)
                chars[ember] = glyphs["trail"]
                styles[ember] = f"bold {colors['beam_dim']}"
            elif sender_release:
                ember = clamp(left - 1)
                chars[ember] = glyphs["echo"]
                styles[ember] = f"bold {colors['shell_dim']}"

            if receiver_ack:
                wink = clamp(right + 1)
                chars[wink] = reply_glyph
                styles[wink] = f"bold {colors['orbit_b']}"
            elif receiver_listen:
                wink = clamp(right + 1)
                chars[wink] = glyphs["trail"]
                styles[wink] = f"bold {colors['shell_dim']}"

            mid = clamp((lane_start + lane_end) // 2)
            if not chars[mid].strip() and abs(mid - head) > 3 and (i // 2) % 2 == 0:
                chars[mid] = glyphs["trail"]
                styles[mid] = f"bold {colors['beam_dim']}"
            return chars, styles

        def build_fast(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            lead  = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head  = clamp(int(lead * (width - 1)))
            echo  = clamp(
                int((0.5 + 0.5 * math.sin((phase * motion["lead_freq"]) + motion["echo_phase"])) * (width - 1))
            )
            pilot = clamp(min(
                width - 1,
                head + int(motion["pilot_offset"] + motion["pilot_amp"] * math.sin(phase * motion["pilot_freq"]))
            ))
            chars = [" "] * width
            styles: dict[int, str] = {}
            glitch = glyphs["glitch"]

            for pos in range(width):
                dist = abs(pos - head)
                if dist == 0:
                    chars[pos] = glyphs["packet"]
                    styles[pos] = f"bold {colors['sweep_core']}"
                elif dist <= 1:
                    chars[pos] = glyphs["core"]
                    styles[pos] = f"bold {colors['core']}"
                elif dist <= 2:
                    chars[pos] = glyphs["near"]
                    styles[pos] = f"bold {colors['near']}"
                elif pos < head and (head - pos) <= 10:
                    tail = head - pos
                    if tail <= 2:
                        chars[pos] = glyphs["near"]
                        styles[pos] = f"bold {colors['near']}"
                    elif tail <= 5:
                        chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                        styles[pos] = f"bold {colors['beam']}"
                    else:
                        chars[pos] = glitch[(pos + i) % len(glitch)]
                        styles[pos] = f"bold {colors['beam_dim']}"
                elif pos > head and (pos - head) <= 2:
                    chars[pos] = glyphs["trail"]
                    styles[pos] = f"bold {colors['sweep_tail']}"
                elif pos < head and (head - pos) <= 14 and (pos + i) % 4 == 0:
                    chars[pos] = glitch[(head - pos + i) % len(glitch)]
                    styles[pos] = f"bold {colors['dust']}"
                elif pos > head and (pos - head) <= 6 and (pos + i) % 3 == 0:
                    chars[pos] = glyphs["dust"]
                    styles[pos] = f"bold {colors['shell_dim']}"
                elif (pos + i) % 9 == 0:
                    chars[pos] = glyphs["dust"]
                    styles[pos] = f"bold {colors['dust']}"

            if not chars[echo].strip():
                chars[echo] = glyphs["echo"]
                styles[echo] = f"bold {colors['beam_dim']}"

            if not chars[pilot].strip():
                chars[pilot] = glyphs["trail"]
                styles[pilot] = f"bold {colors['sweep_tail']}"

            for pos, color in (
                (clamp(head - 12), "orbit_a"),
                (clamp(head - 7), "orbit_b"),
                (clamp(head + 7), "orbit_c"),
            ):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["orbit"]
                styles[pos] = f"bold {colors[color]}"

            if head > 2:
                gate = clamp(head - 2)
                if not chars[gate].strip():
                    chars[gate] = glyphs["gate"]
                    styles[gate] = f"bold {colors['beam']}"

            return chars, styles

        def build_plan(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            cols  = [2, width // 3, (2 * width) // 3, width - 3]
            chars = [" "] * width
            styles: dict[int, str] = {}
            active = int((0.5 + 0.5 * math.sin(phase * motion["active_freq"])) * (len(cols) - 1) + 0.5)
            bridge = 0.5 + 0.5 * math.sin(phase * motion["bridge_freq"])
            prev_idx = max(0, active - 1)
            next_idx = min(len(cols) - 1, active + 1)

            for idx, pos in enumerate(cols):
                if idx < active:
                    chars[pos] = glyphs["done"]
                    styles[pos] = f"bold {colors['beam']}"
                elif idx == active:
                    chars[pos] = glyphs["active"]
                    styles[pos] = f"bold {colors['core']}"
                elif idx == next_idx:
                    chars[pos] = glyphs["next"]
                    styles[pos] = f"bold {colors['near']}"
                else:
                    chars[pos] = glyphs["idle"]
                    styles[pos] = f"bold {colors['beam_dim']}"

            for idx in range(len(cols) - 1):
                a, b = cols[idx], cols[idx + 1]
                progress_pos = clamp(a + 1 + int((b - a - 2) * bridge))
                for pos in range(a + 1, b):
                    ratio = (pos - a) / max(b - a, 1)
                    if idx < active:
                        chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                        styles[pos] = f"bold {colors['beam']}"
                    elif idx == active and ratio <= bridge:
                        if pos == progress_pos:
                            chars[pos] = glyphs["progress"]
                            styles[pos] = f"bold {colors['sweep_core']}"
                        elif pos >= progress_pos - 2:
                            chars[pos] = glyphs["pulse"]
                            styles[pos] = f"bold {colors['sweep_tail']}"
                        else:
                            chars[pos] = glyphs["beam_b"]
                            styles[pos] = f"bold {colors['near']}"
                    elif idx == active:
                        chars[pos] = glyphs["echo"]
                        styles[pos] = f"bold {colors['shell']}"
                    elif (pos + i) % 6 == 0:
                        chars[pos] = glyphs["echo"]
                        styles[pos] = f"bold {colors['shell_dim']}"

            halo = [
                clamp(cols[active] - 2), clamp(cols[active] - 1),
                clamp(cols[active] + 1), clamp(cols[active] + 2)
            ]
            for idx, pos in enumerate(halo):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["echo"] if idx % 2 == 0 else glyphs["pulse"]
                styles[pos] = f"bold {(colors['orbit_a'], colors['orbit_b'], colors['orbit_c'], colors['orbit_a'])[idx]}"

            for pos in (clamp(cols[prev_idx] - 1), clamp(cols[next_idx] + 1)):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["echo"]
                styles[pos] = f"bold {colors['orbit_b']}"
            return chars, styles

        def frame(i: int) -> Text:
            if theme == "fast":
                chars, styles = build_fast(i)
            elif theme == "plan":
                chars, styles = build_plan(i)
            else:
                chars, styles = build_chat(i)

            prefix = f"{spin[i % len(spin)]} "

            out = Text()
            out.append(prefix, style=f"bold {colors['prefix']}")

            for pos, ch in enumerate(chars):
                out.append(ch, style=styles.get(pos, "bold #2A2A2A"))
            return out

        async def settle(i: int) -> None:
            for step in range(7):
                fade = Text(" " * (width + 2), style="bold #202020")
                if step < 5:
                    live.update(frame(i + step))
                    await asyncio.sleep(0.016)
                live.update(fade)
                await asyncio.sleep(0.009)

        with Live(frame(0), console=self.console, refresh_per_second=fps, transient=True) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(frame(tick))
                await asyncio.sleep(1 / fps)

            await settle(tick)

    @classmethod
    def tool_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("tool")
        colors = {
            "edge"      : "bold #6A6256",
            "shell"     : "bold #B9AB96",
            "core"      : "bold #EDE2CE",
            "pulse"     : "bold #DCC8AB",
            "dust"      : "bold #857866",
            "text_peak" : "bold #E6D7BF",
            "text_soft" : "bold #DFD0B8",
            "text_near" : "bold #D7C7AF",
            "text_mid"  : "bold #C5B094",
            "text_fade" : "bold #AB967F",
            "text_dim"  : "bold #8C7B6A"
        }

        text = cls.fit_status_text(text, kind="tool", fallback="function calling")
        span  = max(1, len(text))
        sweep = cls._sway_focus(
            phase,
            span,
            speed=spec.scan_speed,
            pad=spec.scan_pad
        )

        out = cls._tool_status_indicator(
            phase,
            breathe_freq=0.30,
            frame_rate=0.12
        )
        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_sweep_text(
            out,
            text,
            focus=sweep,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius,
            soft_ratio=0.22,
            near_ratio=spec.near_ratio,
            mid_ratio=spec.mid_ratio
        )
        return out

    @classmethod
    def tool_status_static_renderable(cls, text: str) -> Text:
        return cls.tool_status_renderable(0.0, text)

    @classmethod
    def builtin_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("builtin")
        status_shell_motion_scale = 0.76
        status_inner_solid_threshold = 0.87
        status_inner_soft_threshold = 0.70
        status_outer_solid_threshold = 0.92
        status_outer_soft_threshold = 0.77
        status_edge_threshold = 0.83
        stable_colors = {
            "edge"     : "bold #40515D",
            "core"     : "bold #DCE9ED",
            "near"     : "bold #B3CAD3",
            "trail"    : "bold #78949F",
            "dust"     : "bold #4C606B",
            "text"     : "bold #DCEAF0",
            "text_soft": "bold #D2E3E9",
            "text_near": "bold #C2D7DE",
            "text_mid" : "bold #9DB8C2",
            "text_fade": "bold #697F89",
            "text_dim" : "bold #53656E"
        }

        text = cls.fit_status_text(text, kind="builtin", fallback="working")
        colors  = stable_colors
        shell_motion = phase * (spec.shell_freq * status_shell_motion_scale)
        breathe = 0.5 + (0.5 * math.sin(shell_motion))
        left_outer_phase = 0.5 + (0.5 * math.sin(shell_motion - 1.45))
        left_inner_phase = 0.5 + (0.5 * math.sin(shell_motion - 0.75))
        right_inner_phase = 0.5 + (0.5 * math.sin(shell_motion + 0.75))
        right_outer_phase = 0.5 + (0.5 * math.sin(shell_motion + 1.45))
        shell_phase = 0.5 + (0.5 * math.sin(shell_motion + 2.1))
        span = max(1, len(text))
        drift = cls._drift_focus(
            phase,
            span,
            entry_pad=spec.entry_pad,
            exit_pad=spec.exit_pad
        )

        core = cls._status_core_char(
            breathe,
            peak="*",
            high="O",
            mid="o",
            low="."
        )
        left_inner = cls._status_shell_char(
            left_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_inner = cls._status_shell_char(
            right_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        left_outer = cls._status_shell_char(
            left_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        right_outer = cls._status_shell_char(
            right_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        edge_left, edge_right = cls._status_edge_pair(shell_phase, threshold=status_edge_threshold)

        out = cls._build_status_shell(
            edge_left=edge_left,
            edge_right=edge_right,
            left_outer=left_outer,
            left_inner=left_inner,
            core=core,
            right_inner=right_inner,
            right_outer=right_outer,
            edge_style=colors["edge"],
            outer_style=colors["trail"],
            inner_style=colors["near"],
            core_style=colors["core"]
        )
        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_gradient_sweep_text(
            out,
            text,
            focus=drift,
            peak_color="#EEF9FD",
            soft_color="#E5F3F8",
            near_color="#D7EDF5",
            mid_color="#B3CDD8",
            fade_color="#708894",
            dim_color="#566C78",
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius
        )
        return out

    @classmethod
    def thinking_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("wait")
        status_shell_motion_scale = 0.76
        status_inner_solid_threshold = 0.87
        status_inner_soft_threshold = 0.70
        status_outer_solid_threshold = 0.92
        status_outer_soft_threshold = 0.77
        status_edge_threshold = 0.83
        colors = {
            "edge"     : "bold #445856",
            "dot"      : "bold #D7E6E1",
            "dot_soft" : "bold #B5CAC4",
            "dot_dim"  : "bold #748A85",
            "text"     : "bold #DDE7E3",
            "text_near": "bold #A4B5B0",
            "text_tail": "bold #667873",
            "text_dim" : "bold #465652"
        }

        text = cls.fit_status_text(text, kind="wait", fallback="thinking")
        shell_motion = phase * (spec.shell_freq * status_shell_motion_scale)
        breathe = 0.5 + (0.5 * math.sin(shell_motion))
        left_outer_phase = 0.5 + (0.5 * math.sin(shell_motion - 1.7))
        left_inner_phase = 0.5 + (0.5 * math.sin(shell_motion - 0.9))
        right_inner_phase = 0.5 + (0.5 * math.sin(shell_motion + 0.9))
        right_outer_phase = 0.5 + (0.5 * math.sin(shell_motion + 1.7))
        shell_phase = 0.5 + (0.5 * math.sin(shell_motion + 2.2))

        left_outer = cls._status_shell_char(
            left_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        left_inner = cls._status_shell_char(
            left_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_inner = cls._status_shell_char(
            right_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_outer = cls._status_shell_char(
            right_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        core = cls._status_core_char(
            breathe,
            peak="*",
            high="O",
            mid="o",
            low="."
        )
        edge_left, edge_right = cls._status_edge_pair(shell_phase, threshold=status_edge_threshold)

        out = cls._build_status_shell(
            edge_left=edge_left,
            edge_right=edge_right,
            left_outer=left_outer,
            left_inner=left_inner,
            core=core,
            right_inner=right_inner,
            right_outer=right_outer,
            edge_style=colors["edge"],
            outer_style=colors["dot_dim"],
            inner_style=colors["dot_soft"],
            core_style=colors["dot"] if breathe > 0.60 else colors["dot_soft"]
        )
        out.append(cls.status_content_gap(), style=colors["edge"])

        span = max(1, len(text))
        head = (
            (phase * spec.head_speed) % max(1.0, float((span * 2) + spec.cycle_padding))
        ) + spec.head_offset
        if head < float(span - 1):
            tail = spec.tail_reset
        else:
            tail = head - max(0.0, float(span - 1))
        cls._append_progressive_text(
            out,
            text,
            head=head,
            tail=tail,
            peak_style=colors["text"],
            near_style=colors["text_near"],
            tail_style=colors["text_tail"],
            dim_style=colors["text_dim"],
            lead_glow=spec.lead_glow,
            tail_glow=spec.tail_glow
        )
        return out

    @classmethod
    def status_spec(cls, kind: str) -> SweepStatusSpec | ProgressiveStatusSpec:
        tool_status_spec = SweepStatusSpec(
            refresh_per_second=40,
            phase_rate=14.4,
            text_limit=48,
            shell_freq=0.52,
            lead_span=3.1,
            tail_span=6.8,
            peak_radius=0.74,
            near_ratio=0.56,
            mid_ratio=0.90,
            scan_speed=0.2,
            scan_pad=2.6
        )
        builtin_status_spec = SweepStatusSpec(
            refresh_per_second=40,
            phase_rate=15.8,
            text_limit=48,
            shell_freq=0.55,
            lead_span=3.4,
            tail_span=6.6,
            peak_radius=0.78,
            near_ratio=0.46,
            mid_ratio=0.88,
            drift_wobble_amp=0.0,
            drift_wobble_freq=0.0,
            drift_offset=0.0,
            entry_pad=6.0,
            exit_pad=8.4
        )
        thinking_status_spec = ProgressiveStatusSpec(
            refresh_per_second=24,
            phase_rate=19.2,
            text_limit=48,
            shell_freq=0.48,
            head_speed=0.78,
            cycle_padding=4.0,
            head_offset=-1.2,
            tail_reset=-1.6,
            lead_glow=0.36,
            tail_glow=1.0
        )
        if kind == "tool":
            return tool_status_spec
        if kind == "wait":
            return thinking_status_spec
        return builtin_status_spec

    @classmethod
    def status_text_limit(cls, kind: str) -> int:
        console_width = 80
        if cls.console is not None:
            console_width = max(24, int(cls.console.width))

        spec = cls.status_spec(kind)
        if kind == "tool":
            chrome_width = 18
        elif kind == "wait":
            chrome_width = 17
        else:
            chrome_width = 17

        visible_limit = max(12, console_width - chrome_width)
        return min(spec.text_limit, visible_limit)

    @staticmethod
    def status_text_floor(kind: str) -> int:
        if kind == "tool":
            return 16
        if kind == "wait":
            return 12
        return 12

    @classmethod
    def fit_status_text(cls, text: typing.Any, *, kind: str, fallback: str) -> str:
        fitted = cls.truncate_status_text(text, limit=cls.status_text_limit(kind)) or str(fallback)
        return cls.pad_status_text(fitted, kind=kind)

    @classmethod
    def pad_status_text(cls, text: str, *, kind: str) -> str:
        normalized = str(text or "")
        if not normalized:
            return ""

        floor = min(cls.status_text_limit(kind), cls.status_text_floor(kind))
        used_width = cell_len(normalized)
        if used_width >= floor:
            return normalized

        return f"{normalized}{' ' * (floor - used_width)}"

    @classmethod
    def truncate_status_text(cls, text: typing.Any, *, limit: int) -> str:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return ""
        width_limit = max(1, int(limit))
        if cell_len(normalized) <= width_limit:
            return normalized

        ellipsis_char = "…"
        ellipsis_width = cell_len(ellipsis_char)
        if width_limit <= ellipsis_width:
            return ellipsis_char

        body_limit = width_limit - ellipsis_width
        body_chars: list[str] = []
        used_width = 0

        for char in normalized:
            char_width = cell_len(char)
            if used_width + char_width > body_limit:
                break
            body_chars.append(char)
            used_width += char_width

        body = "".join(body_chars).rstrip()
        if not body:
            return ellipsis_char
        return f"{body}{ellipsis_char}"

    @classmethod
    def _append_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_style: str,
        dim_style: str,
        soft_style: str | None = None,
        near_style: str | None = None,
        mid_style: str | None = None,
        fade_style: str | None = None,
        lead_span: float = 2.0,
        tail_span: float = 3.5,
        peak_radius: float = 0.7,
        soft_ratio: float = 0.0,
        near_ratio: float = 0.42,
        mid_ratio: float = 1.0
    ) -> None:
        soft_style = soft_style or peak_style
        near_style = near_style or peak_style
        mid_style = mid_style or dim_style
        fade_style = fade_style or dim_style

        for pos, char in enumerate(text):
            delta = pos - focus
            span = lead_span if delta >= 0 else tail_span
            distance = abs(delta)
            soft_limit = peak_radius + (span * max(0.0, soft_ratio))
            near_limit = peak_radius + (span * max(0.0, near_ratio))
            mid_limit = peak_radius + (span * max(0.0, mid_ratio))

            if distance <= peak_radius:
                style = peak_style
            elif distance <= soft_limit:
                style = soft_style
            elif distance <= near_limit:
                style = near_style
            elif distance <= mid_limit:
                style = mid_style
            elif distance <= peak_radius + span:
                style = fade_style
            else:
                style = dim_style

            out.append(char, style=style)

    @classmethod
    def _append_gradient_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_color: str,
        soft_color: str,
        near_color: str,
        mid_color: str,
        fade_color: str,
        dim_color: str,
        lead_span: float,
        tail_span: float,
        peak_radius: float
    ) -> None:
        stops = (
            (0.00, dim_color),
            (0.18, fade_color),
            (0.44, mid_color),
            (0.72, near_color),
            (0.90, soft_color),
            (1.00, peak_color),
        )

        for pos, char in enumerate(text):
            distance = abs(pos - focus)
            span = lead_span if pos >= focus else tail_span
            if distance <= peak_radius:
                intensity = 1.0
            else:
                falloff = max(0.001, span)
                normalized = min(1.0, (distance - peak_radius) / falloff)
                intensity = 1.0 - cls._smoothstep(normalized)
                if pos < focus:
                    intensity *= 0.95

            out.append(char, style=cls._gradient_text_style(stops, intensity))

    @classmethod
    def _drift_focus(
        cls,
        phase: float,
        span: int,
        *,
        entry_pad: float,
        exit_pad: float
    ) -> float:
        left_pad = max(0.0, float(entry_pad))
        right_pad = max(0.0, float(exit_pad))
        travel = max(1.0, float(max(0, span - 1)) + left_pad + right_pad)
        return (phase % travel) - left_pad

    @classmethod
    def _gradient_text_style(
        cls,
        stops: tuple[tuple[float, str], ...],
        intensity: float
    ) -> str:
        level = max(0.0, min(1.0, float(intensity)))
        if level <= stops[0][0]:
            return f"bold {stops[0][1]}"

        for index in range(1, len(stops)):
            end_level, end_color = stops[index]
            start_level, start_color = stops[index - 1]
            if level <= end_level:
                span = max(0.0001, end_level - start_level)
                local = (level - start_level) / span
                eased = cls._smoothstep(local)
                color = mix_hex_color(start_color, end_color, eased)
                return f"bold {color}"

        return f"bold {stops[-1][1]}"

    @staticmethod
    def _smoothstep(value: float) -> float:
        clamped = max(0.0, min(1.0, float(value)))
        return clamped * clamped * (3.0 - (2.0 * clamped))

    @staticmethod
    def status_content_gap() -> str:
        return "  "

    @staticmethod
    def status_line_prefix() -> str:
        return " "

    @staticmethod
    def status_elapsed_separator() -> str:
        return "  · "

    @classmethod
    def status_line_renderable(
        cls,
        renderable: Text,
        *,
        prefix_style: str = "bold #46545C"
    ) -> Text:
        out = Text()
        prefix = cls.status_line_prefix()
        if prefix:
            out.append(prefix, style=prefix_style)
        out.append_text(renderable)
        return out

    @staticmethod
    def _status_shell_char(
        level: float,
        *,
        solid: str = "o",
        soft: str = ".",
        idle: str = " ",
        solid_threshold: float = 0.90,
        soft_threshold: float = 0.74
    ) -> str:
        if level > solid_threshold:
            return solid
        if level > soft_threshold:
            return soft
        return idle

    @staticmethod
    def _status_core_char(
        level: float,
        *,
        peak: str = "*",
        high: str = "O",
        mid: str = "o",
        low: str = ".",
        super_peak: str | None = None,
        super_threshold: float = 0.94,
        peak_threshold: float = 0.80,
        high_threshold: float = 0.62,
        mid_threshold: float = 0.46
    ) -> str:
        if super_peak is not None and level > super_threshold:
            return super_peak
        if level > peak_threshold:
            return peak
        if level > high_threshold:
            return high
        if level > mid_threshold:
            return mid
        return low

    @staticmethod
    def _status_edge_pair(
        level: float,
        *,
        threshold: float = 0.84,
        active_left: str = "<",
        active_right: str = ">",
        idle_left: str = "[",
        idle_right: str = "]"
    ) -> tuple[str, str]:
        if level > threshold:
            return active_left, active_right
        return idle_left, idle_right

    @classmethod
    def _build_status_shell(
        cls,
        *,
        edge_left: str,
        edge_right: str,
        left_outer: str,
        left_inner: str,
        core: str,
        right_inner: str,
        right_outer: str,
        edge_style: str,
        outer_style: str,
        inner_style: str,
        core_style: str
    ) -> Text:
        out = Text()
        out.append(edge_left, style=edge_style)
        out.append(left_outer, style=outer_style if left_outer.strip() else edge_style)
        out.append(left_inner, style=inner_style if left_inner.strip() else edge_style)
        out.append(cls.status_content_gap(), style=edge_style)
        out.append(core, style=core_style)
        out.append(cls.status_content_gap(), style=edge_style)
        out.append(right_inner, style=inner_style if right_inner.strip() else edge_style)
        out.append(right_outer, style=outer_style if right_outer.strip() else edge_style)
        out.append(edge_right, style=edge_style)
        return out

    @classmethod
    def _sway_focus(
        cls,
        phase: float,
        span: int,
        *,
        speed: float,
        pad: float = 0.0
    ) -> float:
        travel = max(1.0, float(span - 1) + (pad * 2.0))
        progress = 0.5 - (0.5 * math.cos(phase * speed))
        return (progress * travel) - pad

    @classmethod
    def _append_progressive_text(
        cls,
        out: Text,
        text: str,
        *,
        head: float,
        tail: float,
        peak_style: str,
        near_style: str,
        tail_style: str,
        dim_style: str,
        lead_glow: float = 0.4,
        tail_glow: float = 1.2
    ) -> None:
        for pos, char in enumerate(text):
            if char == " ":
                out.append(char, style=dim_style)
                continue

            if pos > head:
                style = near_style if (pos - head) <= lead_glow else dim_style
                out.append(char, style=style)
                continue

            if pos < tail:
                style = tail_style if (tail - pos) <= tail_glow else dim_style
                out.append(char, style=style)
                continue

            out.append(char, style=peak_style)

    @classmethod
    def status_refresh_per_second(cls, kind: str) -> int:
        return int(cls.status_spec(kind).refresh_per_second)

    @classmethod
    def status_interval(cls, kind: str) -> float:
        return 1 / cls.status_refresh_per_second(kind)

    @classmethod
    def status_phase_rate(cls, kind: str) -> float:
        return float(cls.status_spec(kind).phase_rate)

    @classmethod
    def status_step(cls, kind: str) -> float:
        return cls.status_phase_rate(kind)

    @classmethod
    def _tool_status_indicator(
        cls,
        phase: float,
        *,
        subtle: bool = False,
        breathe_freq: float = 0.52,
        frame_rate: float = 0.72
    ) -> Text:
        colors = {
            "edge"  : "bold #6A6256",
            "shell" : "bold #C7B8A1",
            "core"  : "bold #FFF7E7",
            "pulse" : "bold #F2DEC0",
            "dust"  : "bold #8F816E"
        }
        if subtle:
            breathe = 0.46
            left_phase = 0.58
            right_phase = 0.58
            shell_phase = 0.0
        else:
            breathe = 0.5 + (0.5 * math.sin(phase * breathe_freq))
            left_phase = 0.5 + (0.5 * math.sin((phase * breathe_freq) - 0.95))
            right_phase = 0.5 + (0.5 * math.sin((phase * breathe_freq) + 0.95))
            shell_phase = phase * frame_rate

        shell_frames = (
            ("(", ")"),
            ("(", ")"),
            ("(", ")"),
            ("<", ">"),
            ("(", ")"),
            ("{", "}"),
            ("(", ")"),
            ("<", ">"),
            ("(", ")"),
        )
        shell_left, shell_right = ("(", ")") if subtle else shell_frames[
            int(shell_phase) % len(shell_frames)
        ]

        if left_phase > 0.92:
            chamber_left = "o"
        elif left_phase > 0.82:
            chamber_left = "."
        else:
            chamber_left = " "

        if right_phase > 0.92:
            chamber_right = "o"
        elif right_phase > 0.82:
            chamber_right = "."
        else:
            chamber_right = " "

        if subtle:
            core = "o"
            echo_left = " "
            echo_right = " "
        else:
            core = cls._status_core_char(
                breathe,
                super_peak="@",
                peak="*",
                high="O",
                mid="o",
                low="."
            )

            if breathe > 0.91:
                echo_left = "."
                echo_right = "."
            else:
                echo_left = " "
                echo_right = " "
        out = Text()

        out.append("[", style=colors["edge"])
        out.append(echo_left, style=colors["dust"] if echo_left.strip() else colors["edge"])
        out.append(chamber_left, style=colors["dust"])
        out.append(shell_left, style=colors["shell"] if breathe < 0.84 else colors["pulse"])
        out.append(core, style=colors["core"] if breathe > 0.64 else colors["pulse"])
        out.append(shell_right, style=colors["shell"] if breathe < 0.84 else colors["pulse"])
        out.append(chamber_right, style=colors["dust"])
        out.append(echo_right, style=colors["dust"] if echo_right.strip() else colors["edge"])
        out.append("]", style=colors["edge"])
        return out


if __name__ == '__main__':
    pass
