import unittest

from core.update_sources import (
    ANNOUNCEMENTS_URL, CDN_HOSTS, MANIFEST_URL, SOURCE_IDS, SOURCE_MODES,
    safe_transport_url, safe_url, source_host, source_urls,
)


RELEASE_URL = "https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/v1.6.0-preview.7/OsuSkinEditor-v1.6.0-preview.7-windows-x64.exe"


class UpdateSourcesTests(unittest.TestCase):
    def test_fixed_resource_routes_and_modes(self):
        self.assertEqual(SOURCE_IDS, ("github", "ghfast", "ghproxy"))
        self.assertEqual(SOURCE_MODES, ("auto", "github", "ghfast", "ghproxy"))
        for canonical in (ANNOUNCEMENTS_URL, MANIFEST_URL, RELEASE_URL):
            expected = (("github", canonical), ("ghfast", "https://ghfast.top/" + canonical),
                        ("ghproxy", "https://gh-proxy.org/" + canonical))
            self.assertEqual(source_urls(canonical), expected)
            for source, url in expected:
                self.assertEqual(source_urls(canonical, source), ((source, url),))

    def test_arbitrary_resources_and_modes_cannot_create_proxy_routes(self):
        invalid = (
            None, [], "", "https://example.com/a.exe",
            RELEASE_URL.replace("XiaoLan9999", "OtherOwner"),
            RELEASE_URL.replace("OsuSkinEditor/releases", "AnotherProject/releases"),
            RELEASE_URL.replace("https:", "http:"),
            RELEASE_URL.replace("github.com/", "github.com:443/"),
            RELEASE_URL.replace("github.com/", "user:password@github.com/"),
            RELEASE_URL + "?download=1", RELEASE_URL + "#download",
            RELEASE_URL.replace("v1.6.0-preview.7/", "..%2Fv1.6.0-preview.7/"),
            RELEASE_URL.replace(".exe", ".zip"),
            MANIFEST_URL.replace("/main/", "/other-branch/"),
            MANIFEST_URL.replace("manifest.json", "other.json"),
        )
        for canonical in invalid:
            with self.subTest(canonical=canonical), self.assertRaises(ValueError):
                source_urls(canonical)
        for mode in (None, [], "fastest", "AUTO", "https://example.com"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                source_urls(MANIFEST_URL, mode)

    def test_source_host_is_category_specific(self):
        for kind in ("announcements", "updates", "metadata"):
            self.assertEqual(source_host("github", kind), "raw.githubusercontent.com")
        for kind in ("download", "release"):
            self.assertEqual(source_host("github", kind), "github.com")
        self.assertEqual(source_host("ghfast", "updates"), "ghfast.top")
        self.assertEqual(source_host("ghproxy", "download"), "gh-proxy.org")
        for source, kind in (("auto", "updates"), ("evil", "download"), ([], "updates"),
                             ("github", "invalid"), ("ghfast", None)):
            with self.subTest(source=source, kind=kind), self.assertRaises(ValueError):
                source_host(source, kind)

    def test_metadata_allows_only_exact_resource_routes(self):
        for canonical in (ANNOUNCEMENTS_URL, MANIFEST_URL):
            for _source, url in source_urls(canonical):
                self.assertTrue(safe_transport_url(url, canonical=canonical), url)
                self.assertTrue(safe_url(url), url)
                self.assertFalse(safe_transport_url(url, canonical=canonical, download=True))
            other = MANIFEST_URL if canonical == ANNOUNCEMENTS_URL else ANNOUNCEMENTS_URL
            for _source, url in source_urls(other):
                self.assertFalse(safe_transport_url(url, canonical=canonical), url)

    def test_release_routes_remain_bound_to_one_authenticated_exe(self):
        for _source, url in source_urls(RELEASE_URL):
            self.assertTrue(safe_transport_url(url, canonical=RELEASE_URL, download=True))
            self.assertTrue(safe_url(url, download=True))
            self.assertFalse(safe_transport_url(url, canonical=RELEASE_URL))
            self.assertFalse(safe_url(url))
            self.assertFalse(safe_transport_url(url.replace("windows-x64.exe", "other.exe"),
                                                canonical=RELEASE_URL, download=True, redirected=True))
        other = RELEASE_URL.replace("preview.7/", "preview.6/")
        self.assertTrue(safe_url(other, download=True))
        self.assertFalse(safe_transport_url(other, canonical=RELEASE_URL, download=True, redirected=True))

    def test_encoded_proxy_paths_queries_and_alternate_host_spellings_fail_closed(self):
        for canonical, download in ((MANIFEST_URL, False), (RELEASE_URL, True)):
            for source, url in source_urls(canonical):
                mutations = [url + "?download=1", url + "#asset", url.replace("https:", "http:", 1),
                             url.replace("/XiaoLan9999/", "/OtherOwner/"),
                             url.replace("/OsuSkinEditor/", "/OsuSkinEditor/../"),
                             url.replace("/OsuSkinEditor/", "/OsuSkinEditor%2F"),
                             url.replace("/XiaoLan9999/", "/%58iaoLan9999/"),
                             url.replace("https://", "https://user:token@", 1),
                             url.replace("https://", "https://\n", 1),
                             url + "\\extra", url + "\x00"]
                if source != "github":
                    prefix = "https://ghfast.top/" if source == "ghfast" else "https://gh-proxy.org/"
                    host = source_host(source, "download" if download else "updates")
                    mutations.extend((prefix + canonical.replace("https://", "https%3A%2F%2F"),
                                      prefix + canonical.replace("https://", "https:/"),
                                      url.replace(host, host + ".evil.example", 1),
                                      url.replace(host, host + ":444", 1),
                                      url.replace(host, host + ".", 1),
                                      prefix + "https://ghfast.top/" + canonical))
                for mutated in mutations:
                    with self.subTest(source=source, mutated=mutated):
                        self.assertFalse(safe_transport_url(mutated, canonical=canonical,
                                                            download=download, redirected=True))
                        self.assertFalse(safe_url(mutated, download=download, redirected=True))

    def test_cdn_is_only_allowed_for_release_redirects_and_keeps_signed_query(self):
        for host in CDN_HOSTS:
            url = ("https://" + host + "/github-production-release-asset/123/asset-id"
                   "?response-content-disposition=attachment%3B%20filename%3DEditor.exe&sig=opaque")
            self.assertTrue(safe_transport_url(url, canonical=RELEASE_URL, download=True, redirected=True))
            self.assertTrue(safe_url(url, download=True, redirected=True))
            self.assertFalse(safe_transport_url(url, canonical=RELEASE_URL, download=True))
            self.assertFalse(safe_url(url, download=True))
            self.assertFalse(safe_transport_url(url, canonical=MANIFEST_URL, redirected=True))
            self.assertFalse(safe_url(url, redirected=True))
            for mutated in (url.replace("https:", "http:"), url.replace(host, host + ".evil.example"),
                            url.replace(host, "user:token@" + host), url.replace(host, host + ":444"),
                            url + "#fragment", url.replace("%20", " "), url + "\\other"):
                self.assertFalse(safe_transport_url(mutated, canonical=RELEASE_URL,
                                                    download=True, redirected=True), mutated)

    def test_invalid_canonical_cannot_authorize_an_official_cdn(self):
        cdn = "https://release-assets.githubusercontent.com/asset?sig=test"
        for canonical in (None, [], "https://evil.example/asset.exe", RELEASE_URL + "?query"):
            self.assertFalse(safe_transport_url(cdn, canonical=canonical, download=True, redirected=True))
        for url in (None, [], "", "https://localhost/a.exe", "https://ghfast.top/https://example.com/a.exe"):
            self.assertFalse(safe_url(url, download=True, redirected=True))


if __name__ == "__main__":
    unittest.main()
