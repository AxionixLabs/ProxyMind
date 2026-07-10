# -*- coding: utf-8 -*-

from backend.mcp_hub.hub_device.device import Device
from backend.mcp_hub.hub_device.phone import Phone


def test_activity_component_keeps_full_component():
    assert (
        Phone._activity_component(
            "com.neewer.neewerapp",
            "com.neewer.neewerapp/leakcanary.internal.activity.LeakLauncherActivity",
        )
        == "com.neewer.neewerapp/leakcanary.internal.activity.LeakLauncherActivity"
    )


def test_activity_component_builds_package_component():
    assert (
        Phone._activity_component(
            "com.neewer.neewerapp",
            "leakcanary.internal.activity.LeakLauncherActivity",
        )
        == "com.neewer.neewerapp/leakcanary.internal.activity.LeakLauncherActivity"
    )


def test_focus_matches_package_only_without_activity():
    assert Device._focus_matches(
        {
            "package": "com.neewer.neewerapp",
            "activity": "com.neewer.neewerapp/.MainActivity",
        },
        "com.neewer.neewerapp",
        None,
    )


def test_focus_rejects_same_package_different_activity():
    assert not Device._focus_matches(
        {
            "package": "com.neewer.neewerapp",
            "activity": "com.neewer.neewerapp/.MainActivity",
        },
        "com.neewer.neewerapp",
        "leakcanary.internal.activity.LeakLauncherActivity",
    )


def test_focus_matches_target_activity():
    assert Device._focus_matches(
        {
            "package": "com.neewer.neewerapp",
            "activity": "com.neewer.neewerapp/leakcanary.internal.activity.LeakLauncherActivity",
        },
        "com.neewer.neewerapp",
        "leakcanary.internal.activity.LeakLauncherActivity",
    )


def test_focus_matches_relative_activity():
    assert Device._focus_matches(
        {
            "package": "com.neewer.neewerapp",
            "activity": "com.neewer.neewerapp/.MainActivity",
        },
        "com.neewer.neewerapp",
        ".MainActivity",
    )
