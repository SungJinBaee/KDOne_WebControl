"""Run against `python webapp.py --demo`. Never operates a live home."""
from pathlib import Path
import os
from playwright.sync_api import sync_playwright, expect


def main():
    Path("artifacts").mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        base_url = os.environ.get("KDONE_TEST_URL", "http://127.0.0.1:8088").rstrip("/")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        assert page.request.get(base_url + "/api/session").json()["demo"], "Demo only"
        page.goto(base_url + "/", wait_until="networkidle")
        toggle = page.locator("#lights-grid [role=switch]").first
        toggle.wait_for()
        before = toggle.get_attribute("aria-checked")
        toggle.click()
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        expect(toggle).to_have_attribute("aria-checked", "false" if before == "true" else "true")
        page.locator("[data-tab=ventilation]").click()
        for mode in ("Sleep", "Auto"):
            page.locator(f"#ventilation-grid [data-value={mode}]").click()
            expect(page.locator("#confirm-dialog")).not_to_be_visible()
            page.locator(f"#ventilation-grid [data-value={mode}].selected").wait_for()
        page.locator(".schedule-shortcut:visible").first.click()
        page.locator('#sequence-rows [data-step-key="minutes"]').fill("2")
        with page.expect_response(lambda response: response.url.endswith("/api/timers") and response.request.method == "POST") as saved:
            page.locator("#schedule-form button[type=submit]").click()
        job_id = saved.value.json()["id"]
        page.locator("#schedule-dialog").wait_for(state="hidden")
        page.locator(f"[data-edit-timer='{job_id}']").click()
        expect(page.locator('#sequence-rows [data-step-key="minutes"]')).to_have_value("2")
        page.locator('#sequence-rows [data-step-key="minutes"]').fill("3")
        with page.expect_response(lambda response: response.url.endswith(f"/timers/{job_id}/edit")) as updated:
            page.locator("#schedule-form button[type=submit]").click()
        assert updated.value.ok
        page.locator("#schedule-dialog").wait_for(state="hidden")
        page.locator(f"[data-stop-timer='{job_id}']").click()
        expect(page.locator(f"[data-stop-timer='{job_id}']")).to_have_count(0)
        page.locator('[data-kind="automation"]').click()
        expect(page.locator("#timer-device-label")).not_to_be_visible()
        page.locator("#schedule-repeat").select_option("weekdays")
        page.locator('#schedule-days input[value="0"]').check()
        page.locator('#sequence-rows [data-step-key="value"]').select_option("Off")
        page.locator("#add-wait-row").click()
        page.locator("#add-action-row").click()
        rows = page.locator("#sequence-rows .sequence-row")
        expect(rows).to_have_count(3)
        rows.nth(2).locator('[data-step-move="-1"]').click()
        expect(rows.nth(1)).to_contain_text("기기 동작")
        rows.nth(1).locator('[data-step-move="1"]').click()
        rows.nth(2).locator('[data-step-key="device"]').select_option("6")
        rows.nth(2).locator('[data-step-key="value"]').select_option("Off")
        with page.expect_response(lambda response: response.url.endswith("/api/automations") and response.request.method == "POST") as saved:
            page.locator("#schedule-form button[type=submit]").click()
        automation_id = saved.value.json()["id"]
        assert saved.value.ok
        page.locator(f'[data-edit-automation="{automation_id}"]').click()
        expect(page.locator("#sequence-rows .sequence-row")).to_have_count(3)
        page.locator("#sequence-name").fill("Edited automation")
        with page.expect_response(lambda response: response.url.endswith(f"/automations/{automation_id}/edit")) as updated:
            page.locator("#schedule-form button[type=submit]").click()
        assert updated.value.ok
        page.locator("#schedule-dialog").wait_for(state="hidden")
        page.locator(f'[data-automation="{automation_id}"][data-operation="toggle"]').click()
        expect(page.locator(f'[data-automation="{automation_id}"][data-operation="toggle"]')).to_have_attribute("data-enabled", "true")
        page.screenshot(path="artifacts/schedules-desktop.png", full_page=True)
        page.locator(f'[data-automation="{automation_id}"][data-operation="delete"]').click()
        expect(page.locator(f'[data-automation="{automation_id}"]')).to_have_count(0)
        page.locator("[data-tab=heating]").click()
        temp = page.locator("#temp-0")
        temp.fill("21")
        page.locator("[data-temp-input=temp-0]").click()
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        expect(temp).to_have_value("21.0")
        page.locator("[data-tab=hybrid]").click()
        page.locator('#hybrid-grid [data-field="InductionStatus"]').click()
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        expect(page.locator("#hybrid-grid")).to_contain_text("차단됨")
        with page.expect_response(lambda response: response.url.endswith("/api/elevator")):
            page.locator("#elevator").click()
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        page.locator("[data-tab=overview]").click()
        page.locator("#toast").evaluate("element => element.hidden = true")
        page.screenshot(path="artifacts/dashboard-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path="artifacts/dashboard-mobile.png", full_page=True)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), "Mobile horizontal overflow"
        assert not errors, errors
        browser.close()
        print("PASS: browser controls, Sleep transition, temperature, schedules, mobile viewport; no JS errors")


if __name__ == "__main__":
    main()
