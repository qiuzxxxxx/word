# 驻留集
面向考研英语学习的应用平台。因为背单词就像驻留集一样常驻内存，需要每天都要坚持背诵。支持本地桌面应用与多用户网页版。

**[点击查看详细使用说明](docs/USER_GUIDE.md)**

**[在线使用](https://word.qzx1028.space)** · **[下载最新版 word.exe](https://github.com/qiuzxxxxx/word/releases/latest/download/word.exe)** · [版本与更新说明](https://github.com/qiuzxxxxx/word/releases/latest)

在线版无需安装，浏览器打开 https://word.qzx1028.space 即可注册使用，功能与桌面版一致；桌面版适合离线与本地数据备份场景。

支持词汇背诵与复习、考研真题和写作、英语一长难句翻译、六级汉译英、AI 组卷与学习助手。

AI 功能需要联网并自行配置模型 API，可能产生服务费用；AI 内容仅供学习参考。学习数据保存在本机，换电脑或重装前请先备份。

## 关于本项目

本项目前期由作者手工编写完成框架搭建，后期借助 Codex、Trae 等 AI 编程工具进行功能开发与维护。如果你是技术人员，欢迎阅读源码、了解项目结构后进行二次创作。



```powershell
pip install -r requirements.txt
python manage.py runserver
```

访问 `http://127.0.0.1:8000/`。默认启动与宝塔部署共用页面和功能的多用户网页版，首次运行自动初始化 `dist/local-web/` 下的独立数据库。在未登录的窗口打开 `/register/`，测试验证码会输出到运行终端，完整邮件保存在 `dist/local-web/mail/`。

本地开发阶段测试管理员为 `qzx`，初始密码 `123456`。普通测试账号为 `cs`，初始密码 `123456`。账户密码长度支持 6 至 12 位。

调试 Windows 桌面模式时使用 `python manage.py runserver --settings=vocab_project.settings`。桌面打包由 `product/` 目录下的脚本生成。



