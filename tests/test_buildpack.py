import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]
TOKEN = "ghs_test.variable-length.jwt.token"


class BuildpackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = subprocess.check_output(
            ["openssl", "genrsa", "2048"], stderr=subprocess.DEVNULL,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.buildpack = self.root / "buildpack with spaces"
        shutil.copytree(SOURCE, self.buildpack, ignore=shutil.ignore_patterns("__pycache__", "export"))
        self.build = self.root / "build"
        self.cache = self.root / "cache"
        self.env_dir = self.root / "config vars"
        self.mock_bin = self.root / "mock-bin"
        for directory in (self.build, self.cache, self.env_dir, self.mock_bin):
            directory.mkdir()
        (self.build / "Gemfile").write_text('source "https://rubygems.org"\n')
        self.key_base64 = base64.b64encode(self.private_key).decode()
        (self.env_dir / "PRIVATE_GEMS_APP_CLIENT_ID").write_text("Iv.test.client")
        (self.env_dir / "PRIVATE_GEMS_APP_PRIVATE_KEY_BASE64").write_text(self.key_base64)
        (self.env_dir / "PRIVATE_GEMS_APP_REPOSITORIES").write_text("example-org/private-gem")
        self.api_log = self.root / "api.jsonl"
        self.env = os.environ.copy()
        for name in list(self.env):
            if name.startswith("GIT_CONFIG_"):
                del self.env[name]
        self.env.update({
            "PATH": f"{self.mock_bin}:{self.env['PATH']}",
            "MOCK_API_LOG": str(self.api_log),
            "MOCK_TOKEN": TOKEN,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        self.write_executable(self.mock_bin / "curl", r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
config = sys.stdin.read()
with open(os.environ["MOCK_API_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "config": config}) + "\n")
is_token = args[-1].endswith("/access_tokens")
if os.environ.get("FAIL_API") == ("token" if is_token else "installation"):
    print("mock GitHub API error", file=sys.stderr)
    raise SystemExit(22)
if is_token:
    token = os.environ["MOCK_TOKEN"]
    if os.environ.get("INVALID_TOKEN"):
        token += "\n"
    print(json.dumps({"token": token, "expires_at": "2099-01-01T00:00:00Z"}))
else:
    print(json.dumps({"account": {"id": 999}, "id": 2468}))
''')
        self.fallback = self.mock_bin / "fallback-helper"
        self.write_executable(self.fallback, "#!/bin/sh\nprintf 'username=fallback\\npassword=existing-credential\\n\\n'\n")

    def write_executable(self, path, contents):
        path.write_text(contents)
        path.chmod(0o755)

    def compile(self):
        return subprocess.run(
            [str(self.buildpack / "bin/compile"), str(self.build), str(self.cache), str(self.env_dir)],
            env=self.env, capture_output=True, text=True,
        )

    def fill(self, path="example-org/private-gem.git", host="github.com"):
        env = self.env.copy()
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": shlex.quote(str(self.fallback)),
        })
        return subprocess.run(
            ["bash", "-c", 'source "$1/export"\nexec git credential fill', "_", str(self.buildpack)],
            input=f"protocol=https\nhost={host}\npath={path}\n\n",
            env=env, cwd=self.build, capture_output=True, text=True,
        )

    def api_calls(self):
        if not self.api_log.exists():
            return []
        return [json.loads(line) for line in self.api_log.read_text().splitlines()]

    def test_compile_does_not_issue_token_or_write_to_slug_and_cache(self):
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.api_calls(), [])
        self.assertEqual([p.name for p in self.build.iterdir()], ["Gemfile"])
        self.assertEqual(list(self.cache.iterdir()), [])
        export = (self.buildpack / "export").read_text()
        self.assertNotIn(self.key_base64, export)
        self.assertNotIn(TOKEN, export)
        self.assertNotIn(self.key_base64, result.stdout + result.stderr)

    def test_missing_config_fails_before_authentication(self):
        (self.env_dir / "PRIVATE_GEMS_APP_PRIVATE_KEY_BASE64").unlink()
        result = self.compile()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing Config Var", result.stderr)
        self.assertEqual(self.api_calls(), [])

    def test_repository_config_is_required(self):
        (self.env_dir / "PRIVATE_GEMS_APP_REPOSITORIES").unlink()
        result = self.compile()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing Config Var: PRIVATE_GEMS_APP_REPOSITORIES", result.stderr)
        self.assertEqual(self.api_calls(), [])

    def test_repository_config_rejects_urls_and_config_injection(self):
        for repository in (
            "https://github.com/example-org/private-gem",
            "example-org/private-gem\ncredential.helper=other",
            "example-org/private-gem/extra",
            "example-org/private-gem.git",
            "example-org/..",
            ",example-org/private-gem",
            "example-org/private-gem,",
            "example-org/private-gem,,example-org/another-gem",
            "example-org/private-gem, example-org/another-gem",
        ):
            with self.subTest(repository=repository):
                (self.env_dir / "PRIVATE_GEMS_APP_REPOSITORIES").write_text(repository)
                result = self.compile()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid PRIVATE_GEMS_APP_REPOSITORIES", result.stderr)
        self.assertEqual(self.api_calls(), [])

    def test_repositories_must_belong_to_one_installation_owner(self):
        (self.env_dir / "PRIVATE_GEMS_APP_REPOSITORIES").write_text(
            "example-org/private-gem,second-org/another-gem"
        )
        result = self.compile()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use one owner", result.stderr)
        self.assertEqual(self.api_calls(), [])

    def test_multiple_repositories_share_the_token_access_scope(self):
        (self.env_dir / "PRIVATE_GEMS_APP_REPOSITORIES").write_text(
            "example-org/private-gem,example-org/another-gem"
        )
        self.assertEqual(self.compile().returncode, 0)
        for repository in ("private-gem", "another-gem"):
            result = self.fill(f"example-org/{repository}.git")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"password={TOKEN}\n", result.stdout)
        calls = self.api_calls()
        self.assertEqual(len(calls), 4)
        for lookup, creation in ((calls[0], calls[1]), (calls[2], calls[3])):
            self.assertEqual(lookup["args"][-1], "https://api.github.com/repos/example-org/private-gem/installation")
            args = creation["args"]
            self.assertEqual(json.loads(args[args.index("--data") + 1]), {
                "repositories": ["private-gem", "another-gem"],
                "permissions": {"contents": "read"},
            })

    def test_configured_repository_controls_helper_and_token_scope(self):
        (self.env_dir / "PRIVATE_GEMS_APP_REPOSITORIES").write_text("second-org/another-gem")
        self.assertEqual(self.compile().returncode, 0)
        result = self.fill("second-org/another-gem.git")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"password={TOKEN}\n", result.stdout)
        calls = self.api_calls()
        self.assertEqual(calls[0]["args"][-1], "https://api.github.com/repos/second-org/another-gem/installation")
        post = calls[1]["args"]
        self.assertEqual(json.loads(post[post.index("--data") + 1]), {
            "repositories": ["another-gem"],
            "permissions": {"contents": "read"},
        })
        other = self.fill("example-org/private-gem.git")
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertIn("password=existing-credential", other.stdout)
        self.assertEqual(len(self.api_calls()), 2)

    def test_git_get_uses_app_token_with_and_without_dot_git(self):
        self.assertEqual(self.compile().returncode, 0)
        for suffix in ("", ".git"):
            result = self.fill("example-org/private-gem" + suffix)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("username=x-access-token\n", result.stdout)
            self.assertIn(f"password={TOKEN}\n", result.stdout)
            self.assertNotIn("existing-credential", result.stdout)
            self.assertNotIn(TOKEN, result.stderr)
        calls = self.api_calls()
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assertNotIn("Bearer", " ".join(call["args"]))
            self.assertIn("--config", call["args"])
        post = calls[1]["args"]
        self.assertEqual(post[-1], "https://api.github.com/app/installations/2468/access_tokens")
        self.assertEqual(json.loads(post[post.index("--data") + 1]), {
            "repositories": ["private-gem"],
            "permissions": {"contents": "read"},
        })
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual([p.name for p in self.build.iterdir()], ["Gemfile"])

    def test_jwt_is_signed_with_app_key_and_has_expected_claims(self):
        self.assertEqual(self.compile().returncode, 0)
        self.assertEqual(self.fill().returncode, 0)
        config = self.api_calls()[0]["config"]
        jwt = re.search(r"Bearer ([^\"]+)", config).group(1)
        header, payload, signature = jwt.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.assertEqual(json.loads(decode(header))["alg"], "RS256")
        claims = json.loads(decode(payload))
        self.assertEqual(claims["iss"], "Iv.test.client")
        self.assertLessEqual(abs(claims["iat"] - (int(time.time()) - 60)), 10)
        self.assertEqual(claims["exp"] - claims["iat"], 600)
        public_key = subprocess.check_output(
            ["openssl", "pkey", "-pubout"], input=self.private_key,
            stderr=subprocess.DEVNULL,
        )
        (self.root / "public.pem").write_bytes(public_key)
        (self.root / "signature").write_bytes(decode(signature))
        verified = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(self.root / "public.pem"),
             "-signature", str(self.root / "signature")],
            input=f"{header}.{payload}".encode(), capture_output=True,
        )
        self.assertEqual(verified.returncode, 0)

    def test_other_repository_keeps_existing_helper(self):
        self.assertEqual(self.compile().returncode, 0)
        for path, host in (
            ("example-org/public-gem.git", "github.com"),
            ("example-org/private-gem-other.git", "github.com"),
            ("example-org/private-gem.git", "example.com"),
        ):
            result = self.fill(path, host)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("password=existing-credential", result.stdout)
        self.assertEqual(self.api_calls(), [])

    def test_target_subpath_fails_without_issuing_token(self):
        self.assertEqual(self.compile().returncode, 0)
        # Git の URL 設定はサブパスにも一致するが、helper は完全一致以外を拒否する。
        result = self.fill("example-org/private-gem.git/extra")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertEqual(self.api_calls(), [])

    def test_helper_ignores_non_target_and_store_operations(self):
        self.env["PRIVATE_GEMS_APP_ENV_DIR"] = str(self.env_dir)
        self.env["PRIVATE_GEMS_APP_REPOSITORIES"] = "example-org/private-gem"
        for operation, protocol, host, path in (
            ("store", "https", "github.com", "example-org/private-gem.git"),
            ("erase", "https", "github.com", "example-org/private-gem.git"),
            ("get", "http", "github.com", "example-org/private-gem.git"),
            ("get", "https", "github.com.attacker.example", "example-org/private-gem.git"),
            ("get", "https", "github.com", "example-org/other.git"),
        ):
            result = subprocess.run(
                [str(self.buildpack / "lib/git-credential-github-app"), operation],
                input=f"protocol={protocol}\nhost={host}\npath={path}\n\n",
                env=self.env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        self.assertEqual(self.api_calls(), [])

    def test_api_failure_stops_git_without_falling_back(self):
        self.assertEqual(self.compile().returncode, 0)
        for failure in ("installation", "token"):
            self.env["FAIL_API"] = failure
            result = self.fill()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(TOKEN, result.stdout + result.stderr)
            self.assertNotIn("existing-credential", result.stdout + result.stderr)
            self.assertNotIn(self.key_base64, result.stdout + result.stderr)

    def test_invalid_token_response_stops_git(self):
        self.assertEqual(self.compile().returncode, 0)
        self.env["INVALID_TOKEN"] = "1"
        result = self.fill()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_invalid_private_key_fails_without_leaking_input(self):
        invalid_key = "invalid-key-sentinel"
        (self.env_dir / "PRIVATE_GEMS_APP_PRIVATE_KEY_BASE64").write_text(invalid_key)
        self.assertEqual(self.compile().returncode, 0)
        result = self.fill()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(invalid_key, result.stdout + result.stderr)
        self.assertEqual(self.api_calls(), [])


if __name__ == "__main__":
    unittest.main()
