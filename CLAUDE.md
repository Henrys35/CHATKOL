# CHATKOL · 给 agent 的规则

这个文件夹是 CHATKOL：一个在用户自己 Mac 上跑的 KOL 合作看板（本机网页 http://127.0.0.1:<port>）。它读用户的 Lark 邮箱（lark-cli）和飞书多维表格里的博主名单，用用户自己的 Claude 订阅（claude 命令行）读信、写回信开头。

- 第一次装：按 SETUP.md 一步步做。
- 配置在 `config.json`（模板 `config.example.json`，里面每节都有 `_说明`）。程序在 `app/`，模板在 `templates/`，安装和检查脚本在 `scripts/`。

## 必须遵守

1. **密钥不进任何文件，也不进对话。** App Secret、Claude 令牌、lark-cli 的登录都只在用户的 `~/.config/chatkol/` 和 lark-cli 自己的配置里。要输入 Secret 的步骤（`scripts/set_credentials.py`、`lark-cli config init --app-secret-stdin`、`app/setup_claude_token.sh`）让用户自己在终端运行；不要让用户把 Secret 发给你，也不要替他输入。
2. **不替用户发邮件。** 不要运行任何带 `--confirm-send` 的 lark-cli 命令，不要调用看板的发送接口。邮件只在用户自己在网页上点了发送、或者他自己打开了「自动跟进」时才发。
3. **不分享 `data/`。** 里面的本地数据库有完整的邮件内容和博主邮箱，不要上传、不要打包给别人、不要贴进对话。
4. **模板和产品信息只写真实的。** `config.json` 的 `product` 和 `templates/` 里的数字、条件，必须是用户确认过能对外说的；AI 起草只会用这里的说法。
5. **改模板保留结构。** `templates/回复模板.md` 的 `##` 语言标题、`###` 小节名和 `{...}` 占位符不能改名或删掉；只改代码块里的措辞。改完让用户看。
6. 改了 `app/` 里的代码或 `config.json`，要重启服务才生效：`launchctl kickstart -k gui/$(id -u)/com.chatkol.board`（模板改了不用重启）。

## 常用命令

```bash
python3 scripts/check_setup.py           # 逐项检查，哪里不对、怎么改
python3 scripts/prepare_table.py         # 给博主表补齐看板需要的列
bash scripts/run.sh                      # 前台试跑
bash scripts/install_service.sh          # 装成常驻服务（--uninstall 卸载）
python3 app/board.py sync --no-write     # 只同步一次看结果，不写回飞书
tail -f data/logs/board.log              # 看服务日志
```
