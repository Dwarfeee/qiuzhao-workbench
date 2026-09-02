# 版本管理规范（轻量版）

> 目标：用 git 把项目管起来、能回滚、能同步到 GitHub 私有仓库。
> 不想搞太重——能跑、能追溯、不丢东西就行。

## 1. 分支模型

- **`main`** 是唯一长期分支，也是默认分支。
- 小改动（修 bug、改文案、调样式）**允许直接提交到 `main`**。
- 较大的功能 / 重构，**建议**开一个临时分支，例如 `feature/xxx` 或 `fix/xxx`，
  自测没问题后合并回 `main` 并删除该分支。这不是强制的，看心情。

## 2. 提交信息

不强制格式，但推荐加一个前缀，方便以后翻历史：

| 前缀       | 含义                 |
| ---------- | -------------------- |
| `feat:`    | 新功能               |
| `fix:`     | 修 bug               |
| `docs:`    | 文档 / 说明          |
| `refactor:`| 重构（无新功能）     |
| `chore:`   | 杂项（依赖、配置等） |

例：`feat: 精投中心支持从岗位池迁移并加删除按钮`

## 3. 版本号 / Tag（里程碑标记）

采用「宽松语义化版本」`v主版本.次版本.修订`：

- 还在个人折腾阶段 → 从 `v0.x.x` 开始；
- 一个能稳定跑起来的阶段 → 打一个 tag；
- 破坏性改动（接口/数据结构不兼容）→ 主版本 +1。

打 tag 的方式：

```bash
git tag -a v0.1.0 -m "初始化：岗位池 + 精投中心 + 投递规划"
git push origin v0.1.0
```

本仓库首个 tag 即 **`v0.1.0`（初始化）**。

## 4. 同步到远程（GitHub 私有仓库）

```bash
git remote add origin git@github.com:<你的用户名>/qiuzhao-workbench.git
git push -u origin main --tags
```

之后日常：

```bash
git add -A
git commit -m "fix: ..."
git push
```

## 5. 什么进版本库 / 什么不进

进库：源码（`server/`、`web/`）、文档（`PLAN.md`、`USAGE.md`、`VERSIONING.md`）、
配置（`.env.example`、`requirements.txt`、`.gitignore`）。

**不进库**（见 `.gitignore`）：`.env` 密钥、数据库 `jobs.db*`、个人资料
`candidate/`、`resume/`、`desktop/`、`backups/`、Python 缓存。

> 数据库等个人数据请靠应用自身的备份机制，不要塞进 git（二进制且频繁变动，diff 无意义）。

## 6. 回滚

- 只改了工作区还没提交：`git checkout -- <文件>`
- 已提交想撤销本次：`git revert <commit>`
- 想回到某个 tag 的状态：新建分支 `git checkout -b rollback v0.1.0`
