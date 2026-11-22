# 学习工具宝塔操作手册

本手册以 `learn.example.com` 和宝塔目录 `/www/wwwroot/word` 为示例。执行前请换成自己的站点域名和目录。服务器上的应用目录是 `/opt/lexizx`，网站由 Nginx、Gunicorn 和 systemd 提供服务。

## 一、升级现有站点

下载最新的 `lexizx-baota-multiuser.tar.gz` 和 `lexizx-baota-multiuser.tar.gz.sha256`。在宝塔「文件」中把两个文件都上传到 `/www/wwwroot/word/`。文件名要与下方命令一致。

在宝塔终端以 root 整段执行。命令会先校验并解包，再停止服务、备份数据、切换代码和安装新版。**停服务到启动完成期间，网站会短暂不可用。** 终端会打印数据备份目录和旧版代码目录，请保留这两条路径。

```bash
(
set -e
cd /www/wwwroot/word
sha256sum -c lexizx-baota-multiuser.tar.gz.sha256

test -f /opt/lexizx/deploy/baota/install.sh
test -f /var/lib/lexizx/db.sqlite3
test -f /etc/lexizx.env
/www/server/nginx/sbin/nginx -t

staging=$(mktemp -d /opt/lexizx-upload.XXXXXX)
tar --no-same-owner -xzf lexizx-baota-multiuser.tar.gz -C "$staging"
test -f "$staging/lexizx/deploy/baota/install.sh"
mv -- lexizx-baota-multiuser.tar.gz lexizx-baota-multiuser.tar.gz.sha256 "$staging/"

snapshot=$(mktemp -d /opt/lexizx-backup.XXXXXX)
systemctl stop lexizx
cp -a /var/lib/lexizx /etc/lexizx.env "$snapshot/"
test -f "$snapshot/lexizx/db.sqlite3"
test -f "$snapshot/lexizx.env"
printf '数据备份 %s\n旧版代码 %s\n' "$snapshot" "$staging/previous"

mv /opt/lexizx "$staging/previous"
if ! mv "$staging/lexizx" /opt/lexizx; then
  mv "$staging/previous" /opt/lexizx
  systemctl start lexizx
  exit 1
fi
cd /opt/lexizx
bash deploy/baota/install.sh

/www/server/nginx/sbin/nginx -t
/www/server/nginx/sbin/nginx -s reload
curl --fail --silent --show-error --max-time 10 https://learn.example.com/healthz/
)
```

安装脚本会安装依赖、迁移数据库、重新收集静态文件，并维护独立词库模板。已有账号、各自的词库、公告、公共模型和 Resend 配置随原数据库和 `/etc/lexizx.env` 继续使用。接到登录提示时，用服务器上原来的账号密码重新登录。

## 二、升级后验收

1. 最后一条命令应返回 `ok`。然后按 **Ctrl+F5** 强制刷新网页。
2. 用 `zx` 登录 [管理端](https://learn.example.com/control/)。检查原词库、学习记录和已配置的 AI 模型。
3. 用无痕窗口打开 [注册页](https://learn.example.com/register/)。选一个尚未注册、能收信的邮箱测试。验证码 **10 分钟有效**。注册时填写用户名、两次相同的密码，并阅读和勾选《用户许可与使用协议》。完成后用新用户名或邮箱登录，检查是否获得独立词库。
4. 在管理端创建测试公告，点击「预览」核对 Markdown 排版。发布后，在普通账号的学习页面查看。网页版底部应显示 **豫ICP备2026020102号**。
5. 在另一个浏览器用同一测试账号登录，再回第一个浏览器刷新。第一个浏览器应回到登录页。

## 三、日常操作

### 用户管理、维护和公告

- 在 [管理端](https://learn.example.com/control/) 搜索、创建或编辑用户。编辑表单填入新密码会撤销原有登录。封禁立即停用账号，解封后可以重新登录。删除会停用账号并从管理列表移除，学习数据继续留存在该账号的数据库。
- 在「设置维护」开启维护，可填写展示给用户的说明。维护期间普通用户无法登录、注册或调用学习接口。`zx` 可以登录并解除维护。
- 在「新建公告」填写标题与 Markdown 正文。支持标题、强调、列表、引用、链接、表格和代码块。点击「预览」查看排版，勾选「立即发布」才会显示给用户。
- 在「公共 AI 模型」填写显示名称、模型 ID、完整的 OpenAI 兼容 HTTPS 接口地址和密钥，再设定单人和全站的每日调用额度。普通用户可以在个人设置里选用公共模型或自配模型。公共模型供应商按站点提供的密钥结算。

### 验证码邮件

线上已经配置好 Resend 时，升级后可直接测试发信。需要换发信密钥时，在服务器的 `/etc/lexizx.env` 更新以下两项，并执行 `systemctl restart lexizx`。第一行应填写实际的发送权限密钥。 [Resend 域名验证](https://resend.com/docs/dashboard/domains/introduction)

```dotenv
RESEND_API_KEY=替换为实际发信密钥
WORD_EMAIL_FROM=no-reply@auth.example.com
```

同一邮箱两次发送至少间隔 60 秒。收不到时，先到 [Resend 发信记录](https://resend.com/emails) 检查投递状态。若显示已投递，检查收件箱和垃圾邮件；若没有邮件记录，再查看 Resend 的「日志」以及服务器上 `lexizx` 的服务日志。

### 数据备份

- 网页「设置 → 数据管理」可下载当前账号的 JSON 备份，用于迁移或恢复学习数据。恢复会覆盖备份对应的数据。个人 AI 密钥需要在目标站点重新录入。
- 整站备份以升级命令生成的快照为例，保存完整的 `/var/lib/lexizx` 和 `/etc/lexizx.env`。后者含会话签名、AI 加密和发信密钥，只供服务器管理员处理。
- `zx` 的词库在 `/var/lib/lexizx/db.sqlite3`。该文件也存放网站账号、公告和公共模型配置。普通账号的独立数据库在 `/var/lib/lexizx/learners/`。

## 四、排障和回滚

### 检查当前服务

```bash
systemctl status lexizx --no-pager
journalctl -u lexizx -n 100 --no-pager
/www/server/nginx/sbin/nginx -t
curl -fsS https://learn.example.com/healthz/
```

遇到 502，先看 Gunicorn 服务是否启动，再看上面的日志。页面样式或脚本仍是旧版时，可以在 `/opt/lexizx` 重新执行 `bash deploy/baota/manage.sh collectstatic --noinput --clear`，然后按 Ctrl+F5。忘记 `zx` 密码时，在该目录执行 `bash deploy/baota/manage.sh changepassword zx`。

### 恢复到升级前

只有已经切换到新版代码且需要恢复时，才运行下面的命令。按提示填入升级时打印的**完整数据备份目录**和**旧版代码目录**。脚本先校验备份，再将当前版本移到另一处留存，并恢复升级前的快照。回滚后，数据库会回到备份时点。

```bash
(
set -e
read -r -p '粘贴数据备份目录并回车：' snapshot
read -r -p '粘贴旧版代码目录并回车：' previous
snapshot=$(realpath -e "$snapshot")
previous=$(realpath -e "$previous")
case "$snapshot" in
  /opt/lexizx-backup.*|/root/lexizx-backup.*) ;;
  *) echo '数据备份目录格式不对。'; exit 1 ;;
esac
case "$previous" in
  /opt/lexizx-upload.*/previous) ;;
  *) echo '旧版代码目录格式不对。'; exit 1 ;;
esac
test -f "$snapshot/lexizx/db.sqlite3"
test -f "$snapshot/lexizx.env"
test -f "$previous/deploy/baota/install.sh"
test -d /opt/lexizx
test -d /var/lib/lexizx

saved=$(mktemp -d /opt/lexizx-rollback.XXXXXX)
systemctl stop lexizx
mv /opt/lexizx "$saved/code"
mv /var/lib/lexizx "$saved/data"
cp -a /etc/lexizx.env "$saved/"
cp -a "$previous" /opt/lexizx
cp -a "$snapshot/lexizx" /var/lib/lexizx
install -m 0600 "$snapshot/lexizx.env" /etc/lexizx.env
printf '回滚前版本留存于 %s\n' "$saved"

cd /opt/lexizx
bash deploy/baota/install.sh
bash deploy/baota/manage.sh collectstatic --noinput --clear
/www/server/nginx/sbin/nginx -t
/www/server/nginx/sbin/nginx -s reload
curl -fsS https://learn.example.com/healthz/
)
```

如果升级命令还没切换代码就失败，并且旧版本仍在 `/opt/lexizx`，先用 `systemctl start lexizx` 恢复原服务，再处理失败原因。

## 五、在本地测试

在 Windows 的项目目录执行以下命令，再打开 [本地注册页](http://127.0.0.1:8000/register/) 或 [本地管理端](http://127.0.0.1:8000/control/)。

```powershell
python -m pip install -r requirements.txt
python manage.py runserver
```

本地多用户数据库保存在 `dist/local-web/`，验证码显示在运行终端，邮件副本保存在 `dist/local-web/mail/`。本地初始管理员是 `zx`，测试密码是 `Local-preview-zx-2026!`。本地初始普通账号是 `demo`，测试密码是 `Local-preview-demo-2026!`。线上请使用部署时设置的密码。

## 附录：全新服务器安装

OpenCloudOS 9 可用 `dnf install -y python3-pip curl` 安装依赖。Ubuntu 或 Debian 可用 `apt-get install -y python3 python3-venv curl`。将域名解析到服务器，放行 TCP 80 和 443，并通过宝塔安装 Nginx。

把压缩包和校验文件上传到 `/opt/`，完成校验后解压。

```bash
(
set -e
cd /opt
sha256sum -c lexizx-baota-multiuser.tar.gz.sha256
tar --no-same-owner -xzf lexizx-baota-multiuser.tar.gz
cd /opt/lexizx
bash deploy/baota/install.sh learn.example.com zx
)
```

将 `learn.example.com` 换成实际域名。脚本会提示为 `zx` 设置至少 12 个字符的密码。在宝塔添加纯静态站点后，将下面第一条 `include` 放在站点配置的 `server { ... }` 外面，第二条放在 `server { ... }` 里面。若原配置已有 `location /` 或 `client_max_body_size`，移除原有重复项。保留宝塔的 SSL 和 `well-known` 文件验证配置。

```nginx
include /opt/lexizx/deploy/baota/nginx-login-zone.conf;
include /opt/lexizx/deploy/baota/nginx-site.conf;
```

保存站点配置后申请 SSL 证书并启用强制 HTTPS。 [宝塔 SSL 文档](https://docs.bt.cn/getting-started/deploy-ssl/)
