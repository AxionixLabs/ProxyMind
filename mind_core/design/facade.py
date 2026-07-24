# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import random
import typing
import asyncio
import textwrap
from rich.cells import cell_len
from rich.live import Live
from rich.text import Text
from rich.console import Console
from .status.driver import DesignStatusLiveDriver
from .utils import (
    mix_hex_color,
    typewriter as design_typewriter,
    cursor_blink as design_cursor_blink,
    build_file_tree as design_build_file_tree
)
from .fx import (
    download_animation as design_download_animation
)
from .upload import upload_progress_live as design_upload_progress_live
from .intro import (
    IntroFrame,
    intro_frames
)
from mind_nova import const


class Design(DesignStatusLiveDriver):
    """对外保留的设计门面。"""

    def __init__(self, console: Console | None = None) -> None:
        """绑定当前设计实例使用的终端控制台。"""
        self.console: Console = console or Console()

    @staticmethod
    def startup_logo(console: Console) -> None:
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

        console.print(f"[bold {color}]{banner}")
        console.print(const.DECLARE)

    @staticmethod
    def show_intro(console: Console) -> None:
        """显示启动标识并保留最终版本行。"""
        title = const.APP_DESC

        def render_frame(frame_spec: IntroFrame) -> Text:
            out = Text()
            out.append(">_" if frame_spec.prompt_on else "> ", style="dim")

            if frame_spec.title_visible:
                out.append(" ")
                out.append(
                    title[:frame_spec.title_visible],
                    style="bold bright_white",
                )

            if frame_spec.version_visible:
                out.append(f" (v{const.APP_VERSION})", style="dim")

            return out

        frames = intro_frames(title)
        first = frames[0]

        with Live(
            render_frame(first),
            console=console,
            refresh_per_second=30,
            transient=True
        ) as live:
            time.sleep(first.delay_after)
            for next_frame in frames[1:]:
                live.update(render_frame(next_frame))
                time.sleep(next_frame.delay_after)

        final = render_frame(frames[-1])
        console.print(final)
        console.print()

    @staticmethod
    def show_outro(console: Console) -> None:
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
                core_ratio    = 1.0 - (core_distance / max(1, side_width - 1))
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
            glitch_map: dict[int, str]   = glitch_map or {}

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

        with Live(last_frame, console=console, refresh_per_second=30, transient=True) as live:
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

        console.print(last_frame)

    def show_done(self) -> None:
        task_done = textwrap.dedent(f"""\
            [bold #00FF88]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Done             │
            ╰────────────────────────────────────────╯
        """)
        self.console.print(task_done)

    def show_exit(self) -> None:
        task_exit = textwrap.dedent(f"""\
            [bold #FFEE55]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Exit             │
            ╰────────────────────────────────────────╯
        """)
        self.console.print(task_exit)

    def show_fail(self) -> None:
        task_fail = textwrap.dedent(f"""\
            [bold #FF4444]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Fail             │
            ╰────────────────────────────────────────╯
        """)
        self.console.print(task_fail)

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
        renderer: typing.Callable[[str, str], Text] | None = None
    ) -> tuple[str, float]:
        return await design_typewriter(
            live,
            delta,
            out,
            begin_delay,
            final_delay,
            cursor,
            max_lines=max_lines,
            renderer=renderer
        )

    @staticmethod
    async def cursor_blink(
        live: Live,
        out: str,
        cursor: str,
        *,
        max_lines: int | None = None,
        renderer: typing.Callable[[str, bool], Text] | None = None
    ) -> None:
        return await design_cursor_blink(
            live,
            out,
            cursor,
            max_lines=max_lines,
            renderer=renderer
        )

    def build_file_tree(self, file_path: str) -> None:
        return design_build_file_tree(file_path, console=self.console)

    async def download_animation(
        self,
        state: dict[str, typing.Any],
        stop_event: asyncio.Event
    ) -> None:
        return await design_download_animation(
            console=self.console,
            state=state,
            stop_event=stop_event
        )

    async def upload_progress_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        return await design_upload_progress_live(
            console=self.console,
            stop_event=stop_event,
            snapshot=snapshot
        )


if __name__ == '__main__':
    pass
