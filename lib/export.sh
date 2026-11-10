#!/usr/bin/env bash

# Git 設定をファイルに保存すると、Bundler のキャッシュや slug に残る可能性がある。
# 既存の GIT_CONFIG_* に追記し、このビルドの子プロセスにだけ設定を渡す。
github_app_config_index="${GIT_CONFIG_COUNT:-0}"
printf -v github_app_helper '%q' "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/git-credential-github-app"

export "GIT_CONFIG_KEY_${github_app_config_index}=credential.https://github.com.useHttpPath"
export "GIT_CONFIG_VALUE_${github_app_config_index}=true"
github_app_config_index=$((github_app_config_index + 1))

# 対象 URL に限って既存 helper をリセットする。他のリポジトリの認証は維持する。
IFS=',' read -r -a github_app_repositories <<< "${PRIVATE_GEMS_APP_REPOSITORIES:?PRIVATE_GEMS_APP_REPOSITORIES is not set}"
for github_app_repository in "${github_app_repositories[@]}"; do
  for github_app_url in "https://github.com/${github_app_repository}" "https://github.com/${github_app_repository}.git"; do
    export "GIT_CONFIG_KEY_${github_app_config_index}=credential.${github_app_url}.helper"
    export "GIT_CONFIG_VALUE_${github_app_config_index}="
    github_app_config_index=$((github_app_config_index + 1))
    export "GIT_CONFIG_KEY_${github_app_config_index}=credential.${github_app_url}.helper"
    export "GIT_CONFIG_VALUE_${github_app_config_index}=${github_app_helper}"
    github_app_config_index=$((github_app_config_index + 1))
  done
done

export GIT_CONFIG_COUNT="$github_app_config_index"
export GIT_TERMINAL_PROMPT=0
unset github_app_config_index github_app_helper github_app_url github_app_repository github_app_repositories
