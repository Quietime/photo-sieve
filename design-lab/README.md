# 照片快筛：隔离 Web 动效工具环境

主应用仍使用 Python/PySide6。这里的依赖只用于独立的浏览器动效开发与验证，**没有接入桌面应用或手机遥控页的运行时**，也不随 Windows 安装包分发。原应用没有增加 WebView、Node 或 npm 的运行要求。

## 使用

需要 Node 22+、npm。首次安装：`npm ci`；预览：`npm run dev -- --port 4178`；构建：`npm run build`。
在预览服务运行时执行 `npm run verify`，会在真实 Chromium 中验证全部工具，并输出截图与 JSON。
`PLAYWRIGHT_CHROMIUM` 可指定 Chromium 路径；`PHOTOSIEVE_LAB_URL` 可指定已运行的预览地址；`PHOTOSIEVE_LAB_OUTPUT` 可指定验证输出目录。

## 实际安装与接入状态

| 依赖 | 安装版本 | 验证 | 接入位置 |
|---|---|---|---|
| morphicons | 2.0.0 | DOM 路径变形 | 本工具页 |
| lottie-web | 5.13.0 | SVG 动画播放 | 本工具页 |
| gsap | 3.15.0 | 点击触发动效 | 本工具页 |
| @theatre/core | 0.7.2 | 对象属性、时间轴更新 | 本工具页 |
| @theatre/studio | 0.7.2 | 编辑器初始化与打开 | 本工具页 |
| postprocessing | 6.39.5 | Three.js WebGL 合成渲染 | 本工具页 |
| three | 0.186.0 | 后期工具的兼容 peer | 本工具页 |
| Vite / Playwright | 见 package-lock.json | 构建 / 三种屏宽与减少动态效果验证 | 开发验证 |

来源：morphicons 的 npm repository 对应 `guillermolg00/morphicons`；其余均为上述名称的官方 npm 包。锁定版本、下载完整性见 `package-lock.json`。框架 peer 为可选项，没有为了安装图标而引入 React Native/Vue/Svelte。Theatre Studio 的许可证为 AGPL-3.0-only，仅保留在独立工具环境；其余许可证可从各依赖 package.json 查看。

**DREL 尚未确认名称，未安装。** 不把它擅自视为 `@react-three/drei`。

## 本云端环境的技能

技能放在用户级 `~/.agents/skills`，并在 `~/.codex/skills`、`$CODEX_HOME/skills`、本项目 `.agents/skills` 建立发现入口。先检查不存在自定义版本后安装，没有覆盖原有技能。项目入口和 node_modules 不提交；真实来源、commit 和安装文件校验值保存在 `skills-lock.json`。

| 技能 | 实际安装路径 | 来源 / 安装器 | 验证 |
|---|---|---|---|
| frontend-design | `/home/agent/.agents/skills/frontend-design` | anthropics/skills @ dbd4588f9e10 | 名称、资源、SHA-256 通过 |
| web-design-guidelines | `/home/agent/.agents/skills/web-design-guidelines` | vercel-labs/agent-skills @ 063bee94c3f4 | 名称、资源、SHA-256 通过 |
| design-taste-frontend | `/home/agent/.agents/skills/design-taste-frontend` | Leonxlnx/taste-skill @ 6010f91a9935 | 名称、资源、SHA-256 通过 |
| motion-prompt | `/home/agent/.agents/skills/motion-prompt` | showheyohtaki/motion-prompt @ 36f5bdacb1e0 | 名称、资源、SHA-256 通过 |
| regards-art-direction | `/home/agent/.agents/skills/regards-art-direction` | filipwojda/regards-skill @ 61b565be93ef | 名称、资源、SHA-256 通过 |
| anti-ui-slop | `/home/agent/.agents/skills/anti-ui-slop` | discountry/ritmex-skills @ 0be6ec6931d7 | 名称、资源、SHA-256 通过 |
| high-end-visual-design | `/home/agent/.agents/skills/high-end-visual-design` | Leonxlnx/taste-skill-classics @ 5e08c9383dc4 | 名称、资源、SHA-256 通过 |
| redesign-existing-projects | `/home/agent/.agents/skills/redesign-existing-projects` | elayadesign/redesign-skill @ 64627f6ebe7a | 名称、资源、SHA-256 通过 |
| ui-ux-pro-max | `/home/agent/.agents/skills/ui-ux-pro-max` | ui-ux-pro-max-cli@2.15.0 (official bundled Codex template) | 名称、资源、SHA-256 通过 |

UI/UX Pro Max 使用官方 `ui-ux-pro-max-cli@2.15.0 init --ai codex --global --offline` 安装，包含 35 个 CSV 数据文件及搜索脚本。清单的 `upstream_audit_commit` 仅表示核对过的 GitHub 源码，不把它误称为 npm 模板的提交版本。其搜索脚本已实际执行通过。

Motion Prompt 安装了完整固定版本的离线 API 资源（144 项），`info` 与 `search` 已通过；源文件保留在 `~/.local/share/photosieve-design-tools/motion-prompt`。没有将目录中的动画自动移植进应用。

Taste 的方向生成器已实际运行；其余文本技能验证了 frontmatter 名称、可读性、资源及 SHA-256。当前聊天的工具技能目录不支持热刷新，已可直接读取这些本地技能；新会话通过上述标准目录发现。安装这些技能不意味着本次改动套用了全部设计流程。

## Regards MCP

已用 Codex 配置 `https://mcp.regards.design/mcp`。`codex mcp list --json` 确认 `auth_status: not_logged_in`。本聊天没有 Regards 搜索工具。
需要在可完成浏览器回调的 Codex 环境运行 `codex mcp login regards`，使用有 Regards MCP 权限的账号授权；上游 README 写明 MCP 需要 Regards plan。此处没有创建账号、购买套餐或声称在线搜索已接通。未把临时 OAuth 链接和令牌存入项目。

## 渐进模糊与应用

桌面 `GlassSurface`（顶部分类控件、底部操作栏）通过 `glass_material.py` 生成固定的 UI 反光材质，上沿较清晰、向下逐渐扩散。7 个 Gaussian 半径之间以连续权重插值，并以 smoothstep 平滑变化。所有控件共享固定分辨率材质缓存，缩放窗口不重新计算；边框、文字、图标仍以原生分辨率绘制。没有新增空闲动画计时器。

它只处理程序生成的控件材质，**不抓取窗口、原始照片、人脸、图库或预览图**。减少透明度与高对比度模式跳过模糊。实际应用前后截图的照片区域像素完全一致。

本次聚焦验证：渐进模糊、控件动效、键盘与源文件打包、装饰包相关 29 项测试；浏览器验证三个宽度（1120/700/390）、减少动态效果、各库交互与编辑器，无页面异常。没有因这次小改重跑完整功能测试。Windows 新安装包本次未构建。


## 后续方案与新增资源

[页面优化方案与交互原型](proposal/PLAN.md)记录9个设计技能的实际运用、取舍和验证。
新增collage-design已完整安装；[8项设计资源与MCP状态](resources/README.md)区分已安装、已配置待授权与仅有在线入口的项目。
