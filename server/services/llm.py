"""最小 OpenAI 兼容 LLM 客户端（用于按 JD 自动生成定制简历文案）。

配置（在 .env，绝不写死、不落库、不进日志）：

多 Provider 密钥链（推荐，支持「cc switch」式切换/续费）：
  LLM_PROVIDER=deepseek           当前启用的 Provider
  LLM_KEY_DEEPSEEK=sk-xxx         该 Provider 的 Key
  LLM_KEY_OPENAI=sk-xxx           其他 Provider 的 Key（按需，可多个并存）
  LLM_KEY_MOONSHOT=sk-xxx
  LLM_KEY_QWEN=sk-xxx
  LLM_KEY_CUSTOM=sk-xxx

可选覆盖（环境级，一般不用填，用 Provider 预设即可）：
  LLM_BASE_URL=https://...       覆盖 base_url
  LLM_MODEL=xxx                   覆盖 model

向后兼容（单 Key 旧写法，仍可用）：
  LLM_API_KEY=sk-xxx
  LLM_BASE_URL / LLM_MODEL 同上

安全：
- Key 仅经 config.load_env() 读取；
- 任何异常都向上抛出，由调用方决定如何提示用户，不静默吞掉；
- 本模块对外只返回 masked key，绝不返回明文。
"""
from __future__ import annotations

import json

import httpx

from server import config

# ---- Provider 预设（base_url / model 默认值）----
LLM_PROVIDERS = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai":   {"label": "OpenAI",   "base_url": "https://api.openai.com/v1",   "model": "gpt-4o-mini"},
    "moonshot": {"label": "Moonshot (Kimi)", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "qwen":     {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "custom":   {"label": "自定义", "base_url": "", "model": ""},
}


def _mask(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return key[0] + "***"
    return key[:6] + "…" + key[-4:]


def resolve_config() -> dict:
    """解析当前生效的 LLM 配置（provider/key/base/model）。

    优先级：LLM_PROVIDER + LLM_KEY_<PROVIDER> → 否则回退 LLM_API_KEY（旧写法）。
    """
    env = config.load_env()
    provider = (env.get("LLM_PROVIDER") or "").strip().lower()

    # 旧写法：只有 LLM_API_KEY
    if not provider and env.get("LLM_API_KEY"):
        return {
            "provider": "custom",
            "key": env["LLM_API_KEY"].strip(),
            "base": env.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "model": env.get("LLM_MODEL", "gpt-4o-mini"),
            "label": "自定义（旧 LLM_API_KEY）",
        }

    # 新写法：按 Provider 取 key
    key = env.get(f"LLM_KEY_{provider.upper()}", "").strip() if provider else ""
    if not key:  # 回退到旧写法
        key = env.get("LLM_API_KEY", "").strip()
        provider = provider or "custom"
    preset = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["custom"])
    base = env.get("LLM_BASE_URL") or preset.get("base_url") or ""
    model = env.get("LLM_MODEL") or preset.get("model") or ""
    return {
        "provider": provider,
        "key": key,
        "base": base.rstrip("/"),
        "model": model,
        "label": preset.get("label", provider),
    }


def is_configured() -> bool:
    return bool(resolve_config().get("key"))


def list_providers() -> dict:
    """返回所有 Provider 的状态（含 masked key），供密钥管理面板使用。"""
    env = config.load_env()
    active = (env.get("LLM_PROVIDER") or "").strip().lower()
    # 旧写法兼容：若有 LLM_API_KEY 但无 provider，视作 active custom
    if not active and env.get("LLM_API_KEY"):
        active = "custom"
    providers = []
    for pid, p in LLM_PROVIDERS.items():
        key = env.get(f"LLM_KEY_{pid.upper()}", "").strip()
        providers.append({
            "id": pid,
            "label": p["label"],
            "base_url": env.get("LLM_BASE_URL") or p["base_url"],
            "model": env.get("LLM_MODEL") or p["model"],
            "key_masked": _mask(key),
            "has_key": bool(key),
            "active": (pid == active),
        })
    return {"active_provider": active or "custom", "providers": providers,
            "configured": bool(env.get("LLM_API_KEY") or any(
                env.get(f"LLM_KEY_{pid.upper()}", "").strip() for pid in LLM_PROVIDERS))}


def chat(system: str, user: str, *, temperature: float = 0.4,
         json_mode: bool = False, timeout: int = 90) -> str:
    """一次 chat completion，返回助手文本。未配置 Key 抛 RuntimeError。"""
    cfg = resolve_config()
    if not cfg["key"]:
        raise RuntimeError("未配置 LLM Key（在「设置 → LLM 密钥管理」中选择 Provider 并填入 Key）")
    if not cfg["base"] or not cfg["model"]:
        raise RuntimeError(f"Provider「{cfg['label']}」缺少 base_url 或 model，请补全")
    url = f"{cfg['base']}/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {cfg['key']}",
                 "Content-Type": "application/json"},
        json=body, timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def test_connection() -> dict:
    """用一次极简 completion 验证当前 Key 是否可用。"""
    cfg = resolve_config()
    if not cfg["key"]:
        return {"ok": False, "message": "未配置 LLM Key"}
    try:
        out = chat("你是一个 ping 工具。", "只回复 pong 两个字母，不要其他内容。",
                   temperature=0, timeout=20)
        ok = "pong" in out.lower()
        return {"ok": ok, "model": cfg["model"], "provider": cfg["provider"],
                "message": "连接成功" if ok else f"模型返回异常：{out[:60]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)[:200]}
