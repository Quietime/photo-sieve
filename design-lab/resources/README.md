# 云端设计资源与连接状态

核对日期：2026-10-23。实际状态来自HTTP核对、Codex MCP配置和本地依赖检查；网站入口不称为安装完成。

| 资源 | 官方来源 | 用途 | 本云端实际状态 |
|---|---|---|---|
| Scrolltide | https://www.scrolltide.co/ | 网站模板和提示词库 | HTTP200，已加入资源索引；没有虚构同名skill，不是scrolltide.app |
| Mobbin | https://mobbin.com/；https://api.mobbin.com/mcp | UI案例与设计参考 | 官方MCP已配置并检测到OAuth；`not_logged_in`；当前聊天无可调用的在线工具 |
| Fontshare | https://www.fontshare.com/ | 字体选择和授权信息 | HTTP200，资源入口已核对；没有批量下载字体，后续按具体字体授权选取 |
| Coolors | https://coolors.co/ | 配色探索与色板 | 已加入索引；本环境访问返回HTTP403，未声称在线功能可用 |
| Spline | https://spline.design/；https://docs.spline.design/generate/spline-mcp-server | 3D设计与生成 | 官方 `https://mcp.spline.design/mcp` 已配置，OAuth待授权；本次页面优化无需3D SDK，没有额外引入 |
| LottieFiles | https://lottiefiles.com/；https://docs.lottiefiles.com/en/platform/mcp/connect | 动效资源、平台资产 | 官方 `https://mcp.lottiefiles.com/mcp` 已配置，OAuth待授权；既有lottie-web播放器已安装并验证，两者不是同一个服务 |
| Three.js Journey | https://threejs-journey.com/ | Three.js/WebGL课程 | HTTP200，课程资源已核对；没有购买课程，不把已装three npm包当作课程安装 |
| 3dicons | https://3dicons.co/ | 3D图标素材 | HTTP200，资源入口已核对；未批量下载素材，使用时仍核对具体授权 |

## 配置与登录

当前Codex运行配置：`$CODEX_HOME/config.toml`（本环境 `/run/codex-environment/codex-home/config.toml`）。
用户主目录持久配置：`/home/agent/.codex/config.toml`。两处保留此前Regards，并添加上述三个官方端点；已有自定义配置未覆盖。

项目内无凭据的可复现端点配置见 `codex-mcp.toml`。

需要在可完成浏览器回调的同一Codex环境交互执行：

```sh
codex mcp login mobbin
codex mcp login spline
codex mcp login lottiefiles
codex mcp login regards
```

浏览器需登录相应账号并授权。OAuth临时登录URL依赖仍在运行的本机回调，故没有把已停止的临时URL冒充可用链接交付。服务对账号和套餐的具体权限需登录后核验；没有创建账号、购买套餐或声称已获得平台权限。当前会话的工具列表还没有这些MCP工具，需完成授权并刷新连接。

## collage-design 安装

来源：`https://github.com/polgarp/collage-design`，完整仓库根目录。
固定commit：`442f67171ae442de18e0ea3ae5a9767a380bc83c`。
位置：`/home/agent/.agents/skills/collage-design`；在 `~/.codex/skills`、`$CODEX_HOME/skills` 和本项目 `.agents/skills` 均建立发现入口。

使用OpenAI官方skill-installer脚本安装固定commit。其git sparse根路径回退只带入根文件，已从相同commit的完整clone补齐缺失资源，逐文件校验27个上游文件；没有覆盖自定义内容。包含SKILL.md、scripts、references、LICENSE.txt、NOTICE.md、examples和evals。来源与SHA记录见上级 `skills-lock.json`。

依赖检查全通过：

| 依赖 | 真实版本 |
|---|---|
| ImageMagick | 7.1.1-43 |
| Inkscape | 1.4 |
| librsvg / rsvg-convert | 2.60.0 |
| fontconfig | 2.15.0 |
| Python | 3.12.14 |
| Pillow | 12.3.0 |
| NumPy | 2.3.5 |

唯一补装项是rsvg-convert：将匹配已安装librsvg2.60.0的Debian官方二进制包解压到 `~/.local/share/photosieve-design-tools/librsvg`，命令位于 `~/.local/bin/rsvg-convert`。不需要sudo，不替换系统库。来源和下载SHA记录在 `collage-dependencies.json`。

已实际执行上游 `scripts/check_deps.sh`，退出0；另外用临时8×8 SVG验证解析及PNG输出，结束后删除。**没有制作拼贴图，也没有下载拼贴素材。**

核对证据：`source-verification.json`、`configuration-attempts.json`、`mcp-status.json`、`collage-dependency-verification.json`。
