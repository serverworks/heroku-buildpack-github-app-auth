# heroku-buildpack-github-app-auth

GitHub App の installation token で非公開 gem を取得する Heroku classic buildpack。
Heroku-22 / Heroku-24 / Heroku-26 に対応する。

## 使い方

1. GitHub App を対象リポジトリにインストールし、`Contents: read` を許可する。
2. 以下の Config Vars を登録する。
3. この buildpack を `heroku/ruby` の直前に配置する。
4. Gemfile の Git URL を、認証情報を含まない `https://github.com/OWNER/REPOSITORY.git` にする。

| Config Var | 値 |
| --- | --- |
| `PRIVATE_GEMS_APP_CLIENT_ID` | GitHub App の client ID |
| `PRIVATE_GEMS_APP_PRIVATE_KEY_BASE64` | PEM 秘密鍵を改行なしの Base64 にした値 |
| `PRIVATE_GEMS_APP_REPOSITORIES` | `OWNER/REPOSITORY` のカンマ区切りリスト（空白・`.git`・URL は含めない） |

複数リポジトリは、同じ owner・同じ GitHub App installation に属するものを指定する。
Config Vars の登録による dyno 再起動は事前作業として行い、通常のデプロイでは更新しない。

## 仕組み

ビルド中に Git credential helper を設定し、対象リポジトリの認証が必要になった時点で token を発行する。
token は指定リポジトリすべての読み取り権限を持ち、要求ごとに発行する。
秘密鍵や token を slug・キャッシュ・Git 設定ファイルへ書き込まない。

## テスト

```bash
python3 -m unittest discover -s tests -v
tests/run-in-stack 26
tests/run-in-stack 24
tests/run-in-stack 22
```

Docker で実際の GitHub App を使った疎通確認を行う場合は、以下を実行する。

```bash
export PRIVATE_GEMS_APP_CLIENT_ID='YOUR_APP_CLIENT_ID'
export PRIVATE_GEMS_APP_REPOSITORIES='OWNER/REPO_A,OWNER/REPO_B'
tests/smoke-in-stack /absolute/path/to/private-key.pem 26
```
