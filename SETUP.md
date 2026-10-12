# CHATKOL 安装步骤（写给帮用户安装的 agent）

你在帮一位同事在他自己的 Mac 上装 CHATKOL。先读完 AGENTS.md 的规则，再按下面的顺序做。每一步做完告诉用户结果；标着「用户自己做」的，把要做的事写清楚，等他说做完了再继续。

文件夹结构：

```
CHATKOL/
  README.md            给用户看的介绍
  SETUP.md             本文件
  AGENTS.md            你要遵守的规则
  config.example.json  配置模板（带说明）；装好后另存为 config.json
  app/                 看板程序（Python 标准库，不用装第三方包）
  templates/           回信、跟进、私信模板（现在是示例产品的版本，第 7 步改成用户的）
  scripts/             安装和检查用的脚本
  data/                装好后才有：本地数据库和日志（含邮件内容，不要上传、不要分享）
```

## 第 1 步：一次问清楚用户的情况

一条消息问完，用户答完再往下：

1. 发件人：英文名、职位（英文）、公司或产品名、发信用的工作邮箱。
2. 邮箱在 Lark 还是飞书？博主名单的多维表格在 Lark 还是飞书？把表格链接发过来（链接里有 `/base/<app_token>` 和 `table=<table_id>`）。
3. 产品：一两句介绍，能对外说的数据（用户数、播放量之类，只写真实的）。
4. 合作方式和标准条件：买什么（例如博主主账号一条视频）、视频长度、脚本谁写、要不要审、付款时间、送什么（会员、试用）、会不会投流、要不要排他、有没有折扣码分成。
5. 找什么博主，他们的观众要是谁；怎么判断观众不对口。
6. 联系哪些语言的博主：英文、繁中、韩文、日文里选。
7. 有没有定价规则（例如按 CPM 算参考价）、单条价格上限、月预算。
8. 对外绝对不能提的词。

## 第 2 步：装工具

检查并补装（`command -v` 看有没有）：

- Python 3.9 以上：macOS 自带的 `/usr/bin/python3` 就行。看板只用标准库。
- Node.js 和 npm：没有就让用户从 nodejs.org 装，或者 `brew install node`。
- `npm install -g @larksuite/cli`（命令 `lark-cli`）
- `npm install -g @anthropic-ai/claude-code`（命令 `claude`）

## 第 3 步：邮箱应用和登录（用户自己做，你给步骤）

邮箱在哪个平台，就在那个平台的开发者后台建应用：Lark 是 https://open.larksuite.com/app ，飞书是 https://open.feishu.cn/app 。

1. 新建「企业自建应用」，名字例如「<名字>的 CHATKOL」。也可以在终端运行 `lark-cli config init --new --brand lark`（飞书用 `--brand feishu`），跟着浏览器向导建。
2. 「权限管理」里开通这些用户身份权限：
   - `mail:user_mailbox.message:readonly`
   - `mail:user_mailbox.message.address:read`
   - `mail:user_mailbox.message.subject:read`
   - `mail:user_mailbox.message.body:read`
   - `mail:user_mailbox:readonly`
   - `mail:user_mailbox.message:send`
   - `mail:user_mailbox.message:modify`
   - `offline_access`
3. 「安全设置」里加重定向地址时按 lark-cli 提示填（用向导建的会自动处理）。
4. 「版本管理与发布」创建版本、申请发布，等企业管理员审批通过。可用范围要包含用户自己。
5. 审批通过后，用户在终端运行（App Secret 从键盘输入，不要贴进对话）：
   ```bash
   lark-cli config init --app-id <App ID> --app-secret-stdin --brand lark
   ```
   （飞书把 `lark` 换成 `feishu`；用向导建的这一步已经做完了。）
6. 登录邮箱（会给一个链接或二维码，用户在浏览器或手机上授权）：
   ```bash
   lark-cli auth login --scope "mail:user_mailbox.message:readonly mail:user_mailbox.message.address:read mail:user_mailbox.message.subject:read mail:user_mailbox.message.body:read mail:user_mailbox:readonly mail:user_mailbox.message:send mail:user_mailbox.message:modify offline_access"
   ```
7. 建议再运行 `lark-cli config keychain-downgrade`：把 lark-cli 的主密钥从 Mac 钥匙串挪到只有本人能读的文件里，钥匙串被锁时看板也不会停。要跟用户说清楚这个取舍：之后这台 Mac 上以他身份运行的程序都能读这个文件。

验证：`lark-cli auth status --json` 里 `identities.user.status` 是 `ready`。

## 第 4 步：表格应用（用户自己做，你给步骤）

- 表格和邮箱在同一个平台：直接用第 3 步那个应用，再给它加下面的权限、重新发布即可。
- 不在同一个平台（例如邮箱在 Lark、表格在飞书）：在表格所在平台再建一个企业自建应用。

这个应用要开（应用身份）：

- `bitable:app`（读写多维表格，必需）
- `im:message:send_as_bot`，并在「应用能力」里启用机器人（可选：看板出故障时给用户发消息）
- `contact:user.id:readonly`（可选：用邮箱查用户的 open_id，用于上面的提醒）

然后：

1. 发布版本，等管理员审批。
2. 在博主表右上角「…」→「添加文档应用」（或「更多」→「添加协作者」里搜应用名），把这个应用加进来，给「可编辑」。
3. 用户在终端运行 `python3 scripts/set_credentials.py`，按提示输入这个应用的 App ID 和 App Secret（输入 Secret 时不显示）。脚本会先验证，再存到 `~/.config/chatkol/feishu.json`，只有本人能读。

## 第 5 步：写 config.json（你来做）

1. `cp config.example.json config.json`。
2. 按第 1 步的回答填好：`sender`、`mail.since`、`feishu`（`api` 填表格所在平台：飞书 `https://open.feishu.cn/open-apis`，Lark `https://open.larksuite.com/open-apis`；`app_token`、`table_id` 从表格链接里取）、`product`（每个带「例：」的字段都换成用户自己的）、`pricing`（没有定价规则就写 `null`）、`budget_usd`、`templates.by_language`（只留用户要联系的语言）、`addons`（用户的附加条件：按钮文字和默认开关；用不到的删掉）。
3. 要故障提醒的话：`python3 scripts/find_open_id.py <用户登录飞书的邮箱>`，把结果填进 `feishu.alert_open_id`。
4. 给用户看一遍 config.json 里 `sender` 和 `product` 两段，他确认了再往下。

## 第 6 步：准备表格（你来做）

运行 `python3 scripts/prepare_table.py`。它只会补上缺的列，不删不改已有的。然后告诉用户每列的意思：

- 必填：账号（博主的平台账号名）、合作邮箱（走邮件的要有）、发信语言（英文、繁中、韩文、日文）。
- 建议填：平台、主页链接、国家、市场、粉丝数、最近10条中位播放、播粉比、内容主题、人群画像。这些会出现在画像和定价参考里，填得越全，AI 判断越准。
- 联系方式：没有邮箱的填「私信」，会出现在「私信待发」清单里。
- 进度、下一步、最后动态、首封日期由看板自己写；合作状态、心理价位、备注由用户填。

## 第 7 步：按用户的产品改模板（你来做，改完给用户看）

`templates/` 里现在是一个示例产品的版本（文件开头写着是哪个产品），只当例子看。按用户的产品、条件和语气改写：

- `templates/回复模板.md`：回信由这里的小段拼成。
  - 保留每个 `## 语言` 和 `### 小节名` 标题不变，看板靠标题找句子；每个小节第一个代码块是正文，第二个是中文意思（繁中不用）。
  - 占位符保留不动：`{称呼}` `{价格}` `{内容}`；英文的人称和单复数占位符（`{your}` `{You}` `{you're}` `{you'd}` `{them}` `{write}` `{the video}` `{The video}` `{it}` `{script}` `{the post}`）；繁中、韩文、日文的 `{每支}` `{각}` `{各}`；韩文助词 `{으로}` `{을}`；中文意思里的 `{你}` `{你的}` `{视频}`。用法见文件开头的表。
  - 产品相关的句子要改：投流、分成、Pro（送的东西）、排他（竞品类别）、标注、条件。和 config.json 的 `addons` 对应：删掉的开关，模板里那几个小节留着也没关系。
  - 只保留用户要联系的语言那几节也可以，但 `templates.by_language` 里有的语言，回复模板里必须有对应的一节。
- `templates/<语言>.md`：「跟进一」「跟进二」是自动跟进发的信，「私信 · 通用版」是私信清单里复制用的，「首封」只给用户在 EasyKOL 或邮箱里发第一封时参考，看板不用。名字占位符（`{博主昵称}` `{暱稱}` `{닉네임}` `{名前}`）保留。
- 语气：真诚、友好、轻松，像真人在聊合作；英文短段落、用缩写；日文、韩文用敬语但别僵硬；繁中用「你」。
- 改完让用户逐段看（至少英文那节），他点头了才算完。以后他要改措辞，直接改这些文件，看板马上生效。

## 第 8 步：Claude 登录（用户自己做）

先确认 `claude` 能用：用户在终端运行一次 `claude`，用自己的 Claude 账号登录。然后在终端运行：

```bash
bash app/setup_claude_token.sh
```

浏览器里点授权后会生成一个一年期的登录令牌，存到 `~/.config/chatkol/claude_oauth_token`。这样 Mac 钥匙串锁住时 AI 也不会停。

## 第 9 步：检查

运行 `python3 scripts/check_setup.py --ai`。✗ 的逐个处理（每行下面有怎么改），! 的是提醒。全部 ✓ 再继续。

## 第 10 步：试跑

1. 运行 `bash scripts/run.sh`（前台运行；你可以在后台跑它，再看输出）。
2. 让用户打开 http://127.0.0.1:8765 （或 config.json 里的端口）。第一次同步会读这一轮的全部邮件，要几分钟；之后每 5 分钟一次。
3. 和用户一起确认：「全部博主」里能看到表里的人；给发过信的人「进度」对得上；随便点开一个回过信的博主，回复框里有拼好的信。
4. 没问题就停掉试跑（Ctrl+C，或者结束那个进程）。

## 第 11 步：装成常驻服务

运行 `bash scripts/install_service.sh`：开机自动启动、崩了自动拉起，日志在 `data/logs/`。重启、卸载的命令它会打出来。

## 第 12 步：交给用户

告诉用户：

- 地址 http://127.0.0.1:8765 ，从「今天要处理」的卡片开始清。
- 「自动跟进」默认关着，要的话在「该跟进」里打开；发送中可以点「暂停发送」。
- 回信：打开博主 → 选这封做什么、样子、价格、附加条件 → 看一眼 → 发送。要改就写「大意」。
- 改模板、改条件、加语言：直接跟 agent 说，或者自己改 `templates/` 和 `config.json`。
- 出问题先跑 `python3 scripts/check_setup.py`；Lark 登录一周没用会过期，重新 `lark-cli auth login`。

## 常见问题

- 读不到邮箱，提示 keychain：Mac 钥匙串被锁了。在终端运行 `security unlock-keychain ~/Library/Keychains/login.keychain-db`（用户自己输开机密码），或做第 3 步第 7 条。
- 飞书接口 91402 或 1254302 这类权限错误：应用没加进表格、没开 `bitable:app`，或者权限改了之后没重新发布。
- AI 一直没连上：运行 `claude` 看能不能登录；再跑一次 `bash app/setup_claude_token.sh`。
- 端口被占用：在 config.json 改 `port`，重新运行 `bash scripts/install_service.sh`。
