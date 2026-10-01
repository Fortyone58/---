# AI 模型接口核验笔记（2026-10-01）

本笔记记录用于本地配置界面预填值。服务商可能更改型号、端点、免费政策与账号条件；正式使用前应在其官方控制台复核。模型预设允许编辑。

| 服务 | 官方接入文档 | 当前预填模型 ID | OpenAI 兼容基础地址 | 说明 |
| --- | --- | --- | --- | --- |
| DeepSeek | [API 文档](https://api-docs.deepseek.com/) | `deepseek-flash` | `https://api.deepseek.com` | 默认选择。官方模型名不是 `deepseek-chat`；短问答传 `thinking.type=disabled`，但推理模式关闭状态仍应随所选服务确认。 |
| 小米 MiMo | [开放平台](https://platform.xiaomimimo.com/) | `mimo-v2.6-flash` | `https://api.xiaomimimo.com/v1` | 按量计费；注册/邀请赠金需看当前活动和期限，不能称为永久免费。 |
| 阿里云百炼 | [Model Studio 文档](https://help.aliyun.com/zh/model-studio/) | `qwen3.8-flash` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 本预设为北京兼容端点；地域、工作空间专属端点及 API Key 需要匹配。 |
| OpenRouter 免费路由 | [免费路由说明](https://openrouter.ai/docs/guides/routing/model-variants/free) | `openrouter/free` | `https://openrouter.ai/api/v1` | 需要用户自己的 OpenRouter API Key。`openrouter/free` 会路由到当时可用的免费模型；实际模型与限额可能变化。不能承诺稳定/无限免费。 |
| Ollama 本机服务 | [OpenAI 兼容说明](https://docs.ollama.com/api/openai-compatibility) | `qwen3:8b` | `http://127.0.0.1:11434/v1` | 本机须自行安装和下载模型；当前工作电脑未安装 Ollama，不列入已验证可运行环境。 |

这些端点与型号由对应官方文档/控制台公开说明核对，核验日为 2026-10-01。除 DeepSeek 当前预设外，其余服务只核验了文档/标识，不代表本项目已完成真实密钥的连通和原生工具调用验证。测试接口会使用固定问候，不传业务数据；首次业务对话会把完成回答所需的数据发送到配置的服务商。
