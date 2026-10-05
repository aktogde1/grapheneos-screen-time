"""Browser contract tests. All usage data is SYNTHETIC TEST-ONLY.
Run: python web/tests/test_dashboard.py (requires existing Playwright + Chrome).
No device data, network server, dependency installation, or saved snapshots.
"""
import json
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

INDEX = Path(__file__).resolve().parents[1] / "index.html"
ORIGIN = "http://localhost:54321"  # Test-only virtual origin; no listening port.


def synthetic_report(full_days=2):
    """Entirely fabricated, test-only report; never embedded in index.html."""
    dates = [f"2031-04-{i + 2:02d}" for i in range(full_days)]
    full = [{"date": date, "screen_on_ms": 7200000, "unlocked_ms": (i + 1) * 3600000, "unlocks": (i + 1) * 10} for i, date in enumerate(dates)]
    today = {"date": f"2031-04-{full_days + 2:02d}", "screen_on_ms": 28800000, "unlocked_ms": 25200000, "unlocks": 100}
    usage = [(i + 1) * 600000 for i in range(full_days)]
    return {"period_start": dates[0] if dates else today["date"], "period_end_exclusive": today["date"], "period_days": full_days, "collected_at": today["date"] + "T18:25:00+05:00", "screen_days": full, "today_screen": today, "screen_on_ms": sum(d["screen_on_ms"] for d in full), "unlocked_ms": sum(d["unlocked_ms"] for d in full), "technical_package_count": 3, "phone_model": "Synthetic TEST-ONLY device", "user": 12, "app_rows": [
        {"package": "test.only.alpha", "name": "Alpha TEST-ONLY", "category": "Reading", "days": full_days, "week_ms_estimate": sum(usage), "avg_ms_estimate": 1, "last_used": today["date"] + "T12:00:00+05:00", "history_launches": 4, "history_ms": 3600000, "daily_ms": usage + [1800000], "used_dates": dates},
        {"package": "test.only.unknown", "days": 1 if full_days else 0, "week_ms_estimate": 600000 if full_days else 0, "daily_ms": ([0] * (full_days - 1) + [600000] if full_days else []) + [0]},
        {"package": "test.only.idle", "name": "Idle TEST-ONLY", "category": "Other", "days": 0, "week_ms_estimate": 0, "daily_ms": [0] * (full_days + 1)}
    ]}


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1680, "height": 1080}, accept_downloads=True)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.requests = []
        self.report = {"has_data": False}
        self.refresh_result = {"error": "TEST-ONLY: device unavailable"}
        self.context.route("**/*", self.route)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], "Uncaught browser errors")

    def route(self, route):
        req = route.request
        self.requests.append((req.url, req.method, req.headers))
        if req.url == ORIGIN + "/":
            route.fulfill(content_type="text/html", body=INDEX.read_text(encoding="utf-8") if INDEX.exists() else "<!doctype html><title>Not implemented</title>")
        elif req.url.endswith("/api/data"):
            route.fulfill(json=self.report)
        elif req.url.endswith("/api/config"):
            route.fulfill(json={"csrf_token": "synthetic-test-only-token"})
        elif req.url.endswith("/api/refresh"):
            route.fulfill(status=200 if self.refresh_result.get("ok") else 503, json=self.refresh_result)
        else:
            route.abort()

    def open(self):
        self.page.goto(ORIGIN + "/")

    def test_empty_first_run_has_setup_not_measured_zeros(self):
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(self.page.locator("#setup").count(), 1, "First-run setup UI is missing")
        self.assertTrue(self.page.locator("#setup").is_visible())
        self.assertIn("USB", self.page.locator("#setup").inner_text())
        self.assertIn("отлад", self.page.locator("#setup").inner_text().lower())
        self.assertTrue(self.page.locator("#analytics").is_hidden())
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "")
        self.assertNotIn("Во весь браузер", self.page.locator("body").inner_text())
        self.assertEqual(self.page.locator("#export-csv").is_enabled(), False)
        self.assertTrue(self.page.locator("#refresh-data").is_enabled())
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 1680)
        self.assertGreater(self.page.locator("#dashboard").bounding_box()["width"], 1600)


    def test_report_uses_actual_full_days_and_separate_today(self):
        self.report = synthetic_report()
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertTrue(self.page.locator("#analytics").is_visible(), "Collected report was not rendered")
        self.assertTrue(self.page.locator("#setup").is_hidden())
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "1 ч 30 мин")
        self.assertEqual(self.page.locator("#unlock-average").inner_text(), "15")
        self.assertEqual(self.page.locator("#screen-chart [data-day]").count(), 3)
        self.assertIn("2 полных", self.page.locator("#report-subtitle").inner_text())
        self.assertIn("2031", self.page.locator("#snapshot-time").inner_text())
        self.assertIn("18:25", self.page.locator("#snapshot-time").inner_text())
        self.assertIn("Synthetic TEST-ONLY device", self.page.locator("#device-label").text_content())
        self.assertIn("12", self.page.locator("#period-note").text_content())
        self.assertEqual(self.page.locator("#daily-coverage").inner_text(), "2 / 2")
        self.assertEqual(self.page.locator("#everyday-count").inner_text(), "1")
        self.assertIn("15 мин", self.page.locator("#top-apps").inner_text())
        self.assertIn("test.only.unknown", self.page.locator("#app-table").inner_text())
        self.assertNotIn("undefined", self.page.locator("body").inner_text())
        self.page.locator("#day-tabs button", has_text="Сегодня").click()
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "7 ч 00 мин")
        self.assertEqual(self.page.locator("#unlock-average").inner_text(), "100")
        self.assertIn("30 мин", self.page.locator("#top-apps").inner_text())
        self.page.locator("#screen-mode").click()
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "8 ч 00 мин")
        self.page.locator("#day-tabs button").first.click()
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "2 ч 00 мин")
        self.assertTrue(self.page.locator("#export-csv").is_enabled())


    def test_search_category_sort_daily_filter_and_csv_export(self):
        self.report = synthetic_report()
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.page.locator("#search").fill("unknown")
        self.assertEqual(self.page.locator("#app-table tr[data-app]").count(), 1, "Search did not filter apps")
        self.page.locator("#search").fill("")
        self.page.locator("#categories [data-category='Reading']").click()
        self.assertEqual(self.page.locator("#app-table tr[data-app]").count(), 1)
        self.assertEqual(self.page.locator("#category-filter").input_value(), "Reading")
        self.page.locator("#reset-category").click()
        self.page.locator("#only-daily").check()
        self.assertEqual(self.page.locator("#app-table tr[data-app]").count(), 1)
        self.page.locator("#only-daily").uncheck()
        self.page.locator("#hide-zero").check()
        self.assertEqual(self.page.locator("#app-table tr[data-app]").count(), 2)
        self.page.locator("#hide-zero").uncheck()
        self.page.locator("#sort").select_option("name")
        self.assertIn("Alpha", self.page.locator("#app-table tr").first.inner_text())
        self.page.locator("#search").fill("NOT PRESENT")
        self.assertIn("Ничего не найдено", self.page.locator("#app-table").inner_text())
        self.page.locator("#search").fill("alpha")
        self.page.locator("#day-tabs button", has_text="Сегодня").click()
        with self.page.expect_download() as download_info:
            self.page.locator("#export-csv").click()
        download = download_info.value
        self.assertTrue(download.suggested_filename.endswith(".csv"))
        csv = Path(download.path()).read_text(encoding="utf-8-sig")
        self.assertIn('"Alpha TEST-ONLY"', csv)
        self.assertNotIn("test.only.idle", csv)
        self.assertIn('"30.000"', csv)
        self.assertIn("Сегодня", csv)
        self.assertIn("2", csv)

    def test_app_details_keyboard_and_close(self):
        self.report = synthetic_report()
        self.open()
        self.page.wait_for_load_state("networkidle")
        row = self.page.locator("#app-table tr[data-app='test.only.alpha']")
        row.focus()
        row.press("Enter")
        self.assertTrue(self.page.locator("#app-detail").is_visible(), "Keyboard activation did not open details")
        self.assertIn("15 мин", self.page.locator("#detail-meta").inner_text())
        self.assertIn("2 дней из 2", self.page.locator("#detail-meta").inner_text())
        self.assertEqual(self.page.locator("#detail-week .mini-day").count(), 3)
        self.assertIn("Сегодня", self.page.locator("#detail-week").inner_text())
        self.page.locator("#close-detail").click()
        self.assertTrue(self.page.locator("#app-detail").is_hidden())
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.app"), "test.only.alpha")


    def test_refresh_collects_with_same_origin_token_and_keeps_report_on_error(self):
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.refresh_result = {"ok": True, "data": synthetic_report()}
        self.page.locator("#refresh-data").click()
        self.page.wait_for_function("document.querySelector('#refresh-status').textContent.includes('Данные обновлены')")
        posts = [request for request in self.requests if request[1] == "POST"]
        self.assertEqual(len(posts), 1, "Refresh did not POST to the API")
        self.assertEqual(posts[0][0], ORIGIN + "/api/refresh")
        self.assertEqual(posts[0][2].get("x-dashboard-token"), "synthetic-test-only-token")
        self.assertTrue(self.page.locator("#analytics").is_visible())
        self.assertTrue(self.page.locator("#setup").is_hidden())
        self.assertIn("обновлены", self.page.locator("#refresh-status").inner_text())
        self.assertTrue(self.page.locator("#refresh-data").is_enabled())
        self.refresh_result = {"error": "TEST-ONLY: device unavailable"}
        self.page.locator("#refresh-data").click()
        self.page.wait_for_function("document.querySelector('#refresh-status').textContent.includes('device unavailable')")
        self.assertIn("TEST-ONLY: device unavailable", self.page.locator("#refresh-status").inner_text())
        self.assertIn("Предыдущ", self.page.locator("#refresh-status").inner_text())
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "1 ч 30 мин")
        self.assertTrue(self.page.locator("#refresh-data").is_enabled())
        self.assertTrue(all(url.startswith(ORIGIN) for url, _, _ in self.requests))
        self.assertEqual(len(self.context.pages), 1)


    def test_untrusted_labels_are_text_and_csv_formulas_are_neutralized(self):
        self.report = synthetic_report()
        self.report["app_rows"][0]["name"] = "=1+1"
        self.report["app_rows"][0]["category"] = "\t=2+2"
        self.report["app_rows"][1]["name"] = '<img src=x onerror="window.TEST_INJECTION=true">'
        self.report["app_rows"][1]["package"] = 'test.only.quote\"<svg onload="window.TEST_INJECTION=true">'
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(self.page.locator("#dashboard img, #dashboard svg").count(), 0)
        self.assertFalse(self.page.evaluate("Boolean(window.TEST_INJECTION)"))
        with self.page.expect_download() as download_info:
            self.page.locator("#export-csv").click()
        csv = Path(download_info.value.path()).read_text(encoding="utf-8-sig")
        self.assertIn("\"'=1+1\"", csv, "Spreadsheet formula was not neutralized")
        self.assertIn("\"'\t=2+2\"", csv)
        self.assertEqual(self.page.evaluate("localStorage.length"), 0)


    def test_mobile_and_wide_layout_seven_days_no_page_overflow(self):
        self.report = synthetic_report(7)
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(self.page.locator("#screen-chart button").count(), 8)
        self.assertEqual(self.page.locator("#daily-coverage").inner_text(), "7 / 7")
        for width in (320, 360, 390, 760, 1920):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 1000})
                self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
                expected_columns = 1 if width <= 760 else 2
                self.assertEqual(self.page.locator(".grid").first.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length"), expected_columns)
                self.assertGreater(self.page.locator("#dashboard").bounding_box()["width"], width - 50)
                self.page.locator("#day-tabs button", has_text="Сегодня").click()
                self.assertEqual(self.page.locator("#screen-average").inner_text(), "7 ч 00 мин")
                if width <= 760:
                    self.assertGreaterEqual(self.page.locator("#refresh-data").bounding_box()["height"], 44)
                self.page.locator("#day-tabs button").first.click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator("#search").fill("unknown")
        self.assertEqual(self.page.locator("#app-table tr[data-app]").count(), 1)
        self.page.locator("#app-table tr[data-app]").click()
        self.assertTrue(self.page.locator("#app-detail").is_visible())
        self.page.locator("#close-detail").press("Escape")
        self.assertTrue(self.page.locator("#app-detail").is_hidden())

    def test_today_only_has_no_fake_full_day_average(self):
        self.report = synthetic_report(0)
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertTrue(self.page.locator("#analytics").is_visible())
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "—")
        self.assertEqual(self.page.locator("#unlock-average").inner_text(), "—")
        self.assertEqual(self.page.locator("#everyday-count").inner_text(), "—")
        self.page.locator("#day-tabs button", has_text="Сегодня").click()
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "7 ч 00 мин")
        self.assertNotRegex(self.page.locator("body").inner_text(), r"NaN|undefined|Infinity")

    def test_load_network_error_stays_honestly_empty(self):
        self.context.route("**/api/data", lambda route: route.abort())
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertTrue(self.page.locator("#setup").is_visible())
        self.assertTrue(self.page.locator("#analytics").is_hidden())
        self.assertIn("Не удалось", self.page.locator("#refresh-status").inner_text())
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "")

    def test_malformed_report_never_looks_collected(self):
        self.report = {"screen_days": [], "app_rows": []}
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertTrue(self.page.locator("#setup").is_visible())
        self.assertTrue(self.page.locator("#analytics").is_hidden())
        self.assertIn("формат", self.page.locator("#refresh-status").inner_text())

    def test_shipped_html_has_no_embedded_report_private_paths_or_endpoint(self):
        text = INDEX.read_text(encoding="utf-8")
        for marker in ("phone-data", "C:/Users/", "C:\\Users\\", "127.0.0.1:", "localhost:", "2031-04", "test.only."):
            self.assertNotIn(marker, text)
        self.assertNotRegex(text, r"<script[^>]+src=|<link[^>]+href=|https?://")


    def test_refresh_clears_stale_details_and_removed_selection(self):
        self.report = synthetic_report()
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.page.locator("#day-tabs button", has_text="Сегодня").click()
        self.page.locator("#categories [data-category='Reading']").click()
        self.page.locator("#top-apps [data-app='test.only.alpha']").click()
        self.assertTrue(self.page.locator("#app-detail").is_visible())
        newer = synthetic_report(1)
        newer["app_rows"] = []
        self.refresh_result = {"ok": True, "data": newer}
        self.page.locator("#refresh-data").click()
        self.page.wait_for_function("document.querySelector('#refresh-status').textContent.includes('Данные обновлены')")
        self.assertTrue(self.page.locator("#app-detail").is_hidden(), "Old app details still shown after refresh")
        self.assertEqual(self.page.locator("#category-filter").input_value(), "")
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "1 ч 00 мин")
        self.assertIn("1 полных", self.page.locator("#report-subtitle").inner_text())
        self.assertIn("Ничего не найдено", self.page.locator("#app-table").inner_text())


    def test_categories_label_the_scope_and_chart_uses_available_height(self):
        self.report = synthetic_report(7)
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertIn("7 полных дней", self.page.locator("#categories-title").text_content() if self.page.locator("#categories-title").count() else "", "Category totals have no period label")
        chart_height = self.page.locator("#screen-chart").bounding_box()["height"]
        peak_height = self.page.locator("#screen-chart .peak .column").bounding_box()["height"]
        self.assertGreater(peak_height, chart_height * .6)
        self.page.locator("#screen-chart [data-day='0']").click()
        self.assertIn("02.04", self.page.locator("#categories-title").text_content())
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "1 ч 00 мин")


    def test_initial_load_disables_refresh_to_prevent_stale_response_race(self):
        observations = []
        def loading_route(route):
            observations.append(self.page.locator("#refresh-data").is_disabled())
            route.fulfill(json={"has_data": False})
        self.context.route("**/api/data", loading_route)
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(observations, [True], "Refresh remains clickable while initial report is pending")
        self.assertTrue(self.page.locator("#refresh-data").is_enabled())


    def test_declared_period_days_is_dynamic_not_a_fixed_week(self):
        self.report = synthetic_report()
        self.report["period_days"] = 1  # Contract test: explicit denominator takes precedence.
        self.open()
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "3 ч 00 мин")
        self.assertIn("Дней / 1", self.page.locator("#col-days").inner_text())
        self.page.locator("#day-tabs button", has_text="Сегодня").click()
        self.assertEqual(self.page.locator("#screen-average").inner_text(), "7 ч 00 мин")


if __name__ == "__main__":
    unittest.main(verbosity=2)
