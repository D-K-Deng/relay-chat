import { language, t } from './i18n.js';

// Only for Relay's own status and error fields. Chat text and tool output keep their original language.
const messages = {
  "Could not remove all selected files. Close any open files and try again.": "部分文件未能删除。请关闭正在使用的文件后重试。",
  "The chat folder location changed. Choose history-only deletion or restore the folder.": "聊天文件夹的位置已变更。请仅删除记录，或先恢复文件夹。",
  "This chat folder is shared with a project. Reopen the delete dialog to keep that folder.": "聊天文件夹正与项目共用。请重新打开删除窗口以保留该文件夹。",
  "The saved result folder changed. Choose history-only deletion or restore the folder.": "已保存的结果文件夹发生变化。请仅删除记录，或先恢复文件夹。",
  "Running terminal command…": "正在运行终端命令…",
  "Waiting for your review…": "等待你审阅…",
  "Working in the project…": "正在处理项目文件…",
  "Checking the proposed action…": "正在检查拟执行的操作…",
  "Generation stopped.": "已停止生成。",
  "The response reached its output limit.": "回复已达输出上限。",
  "The model used its output budget during reasoning. Try a lower Effort or a shorter prompt.": "模型在推理时用完了输出额度。请降低推理强度或缩短提问。",
  "The response was cut short by OpenAI's content filter.": "OpenAI 的内容过滤机制中止了回复。",
  "OpenAI ended the response before it completed.": "OpenAI 提前结束了回复。",
  "The connection ended early. The partial response is available to copy or regenerate.": "连接提前结束。你可以复制已有内容，或重新生成回复。",
  "The connection to OpenAI was interrupted. You can retry the response.": "与 OpenAI 的连接已中断，可以重新生成回复。",
  "The connection ended early. Try again.": "连接提前结束，请重试。",
  "Could not read the OpenAI response. Check the network or HTTPS_PROXY setting, then retry.": "无法读取 OpenAI 的回复。请检查网络或 HTTPS_PROXY 设置后重试。",
  "The workspace tool limit was reached. Continue to resume.": "本轮已达工作区工具调用上限，点击「继续」可接着处理。",
  "Relay could not finish this response. You can retry it.": "Relay 未能完成回复，可以重试。",
  "This API key is invalid or has been revoked. Update the local key, then regenerate the response.": "API 密钥无效或已被撤销。请更新本机密钥后重新生成回复。",
  "API credits exhausted. Check the balance, expiry, and organization in OpenAI Billing.": "API 额度已用尽。请在 OpenAI 账单中检查余额、有效期和组织。",
  "The organization or project has reached its usage or spending limit. Check the limits in OpenAI Billing.": "组织或项目已达到用量或支出上限。请在 OpenAI 账单中查看限额。",
  "The rate limit was reached. Wait a moment before retrying.": "已达请求速率上限，请稍后重试。",
  "Relay continued from saved progress after a rate limit. The continuation used no tools.": "限流后已根据已有进展续写，续写阶段未调用工具。",
  "This model or resource is unavailable to the project. Choose an available Responses model and check the key permissions.": "此项目无法使用该模型或资源。请选择可用的 Responses 模型，并检查密钥权限。",
  "The request exceeds the model context limit. Include fewer messages or shorten the attachment.": "请求超出模型上下文上限。请减少消息数量或缩短附件内容。",
  "OpenAI is temporarily unavailable. Try again later.": "OpenAI 暂时不可用，请稍后重试。",
  "OpenAI could not complete the request.": "OpenAI 未能完成请求。",
  "Connect your OpenAI API key to load models.": "请连接 OpenAI API 密钥以加载模型。",
  "Open the setup guide and follow step 2 to connect your API key.": "请打开设置指南，按照第 2 步连接 API 密钥。",
  "OpenAI could not list models. Check the API key and its Models read permission.": "无法列出模型。请检查 API 密钥是否有读取模型的权限。",
  "OpenAI could not list models. Try reloading the list.": "无法列出模型，请重新加载列表。",
  "Could not reach the OpenAI model list. Check the connection and try again.": "无法获取 OpenAI 模型列表，请检查网络后重试。",
  "Could not read this attachment.": "无法读取此附件。",
  "Enter a text message and attach no more than four files.": "请输入文字，每条消息最多可添加四个附件。",
  "Choose a PNG, JPEG, WebP, or GIF image up to 3 MB.": "请选择 3 MB 以内的 PNG、JPEG、WebP 或 GIF 图片。",
  "Choose a PDF up to 3 MB.": "请选择 3 MB 以内的 PDF。",
  "Attach a CSV as text instead of a workbook.": "请将表格导出为 CSV，以文本附件发送。",
  "Text attachments can contain up to 100,000 characters each.": "每个文本附件最多支持 100,000 个字符。",
  "Enter a message or add an attachment.": "请输入消息或添加附件。",
  "The latest message exceeds the model's input budget. Shorten it or remove an attachment.": "最新消息超出模型输入上限。请缩短文字或移除附件。",
  "This conversation includes a PDF. Choose a model that supports PDF inputs.": "此聊天包含 PDF，请选择支持 PDF 输入的模型。",
  "Choose a model that supports web search.": "请选择支持联网搜索的模型。",
  "Web search needs Low reasoning effort or higher with this model.": "使用此模型进行联网搜索需要低或更高的推理强度。",
  "This request is too large. Shorten the context or remove an attachment.": "请求过大，请缩短上下文或移除附件。",
  "Could not reach OpenAI for transcription. Check your connection and try again.": "无法连接 OpenAI 进行转写，请检查网络后重试。",
  "Record a WebM, MP4, Ogg or WAV audio clip up to 25 MB.": "请录制 25 MB 以内的 WebM、MP4、Ogg 或 WAV 音频。",
  "No speech was transcribed. Try recording again.": "没有识别到语音，请重新录制。",
  "Enter an update for this run.": "请输入本轮补充指令。",
  "This run is not accepting updates. Send your text as a new message.": "本轮目前无法接收补充指令，请作为新消息发送。",
  "The previous update is queued. Wait for its confirmation before adding another.": "上一条指令正在排队。请等确认后再补充。",
  "Could not send the update. Your draft can be sent again.": "补充指令发送失败，可以重新发送草稿。",
  "This response has finished. Send your update as a new message.": "本轮已结束。请将补充内容作为新消息发送。",
  "WebSocket unavailable. This reply uses HTTPS.": "WebSocket 不可用，本轮已使用 HTTPS 连接。",
  "WebSocket continuation expired. This reply uses HTTPS.": "WebSocket 续传已过期，本次回复通过 HTTPS 继续。",
  "The live connection ended. Retry the response.": "实时连接已结束，请重新生成回复。",
  "Context compaction was stopped.": "上下文压缩已停止。",
  "Choose an available chat model.": "请选择可用的聊天模型。",
  "Check the context length and maximum output settings.": "请检查上下文长度和最大输出设置。",
  "Enter a nonnegative USD balance.": "请输入不小于零的美元余额。",
  "Choose a model before setting rates.": "请先选择模型，再设置费率。",
  "Enter three nonnegative prices in USD per million tokens.": "请填写三项不小于零的费率，单位为美元 / 百万 token。",
  "This local Codex session could not be found.": "找不到这条本地 Codex 会话。",
  "There are no user messages or assistant replies to import.": "没有可导入的用户消息或助手回复。",
  "Chat not found.": "找不到此聊天。",
  "There is no question to regenerate.": "没有可以重新生成回复的问题。",
  "Choose a reply to branch from.": "请选择要从哪条回复创建分支。",
  "Enter a title.": "请输入标题。",
  "A response is already in progress in this chat.": "此聊天正在生成回复。",
  "Stop the response before deleting this conversation.": "请先停止生成，再删除此聊天。",
  "Choose a workspace access mode.": "请选择工作区访问模式。",
  "Choose a workspace access mode for this chat.": "请为此聊天选择工作区访问模式。",
  "Choose a project folder first.": "请先选择项目文件夹。",
  "Choose the workspace folder for this chat before using file tools.": "请先选择此聊天的工作文件夹，再使用文件工具。",
  "The workspace location changed. Choose the project folder again.": "工作区位置已更改，请重新选择项目文件夹。",
  "Workspace access changed. Check this chat's workspace selection.": "工作区权限已改变，请检查此聊天的工作区设置。",
  "Stop the active turn before changing its workspace.": "请先停止当前任务，再切换工作区。",
  "Approve or decline this workspace action.": "请批准或拒绝此工作区操作。",
  "This change is no longer waiting for review.": "这项修改已经完成审阅。",
  "A fixed read-only Git query is permitted by the local command policy.": "本地命令规则允许此只读 Git 查询。",
  "This command may run project code or Git helpers with your user permissions.": "此命令可能会以你的用户权限运行项目代码或 Git 辅助程序。",
  "Manual approval is selected for this chat.": "此聊天使用手动审批。",
  "You declined an earlier change to this file during this turn.": "你在本轮已拒绝过对此文件的修改。",
  "The complete diff exceeds the automatic review size limit.": "完整差异超出了自动审阅的大小限制。",
  "The independent reviewer is checking the proposed file change.": "正在独立审阅拟议的文件修改。",
  "Created file": "已创建文件",
  "Applied edit": "已应用修改",
  "File already matches.": "文件内容已经匹配。",
  "Turn stopped before writing.": "已在写入前停止。",
  "User declined the proposed change.": "你已拒绝此修改。",
  "Command stopped before execution.": "命令已在执行前停止。",
  "Command declined.": "命令已被拒绝。",
  "Command stopped.": "命令已停止。",
  "The working directory changed during review.": "工作目录在审阅期间发生了变化。",
  "This command requires the Review edits workspace mode and a Run decision.": "请启用「审阅修改」工作区模式，并手动批准此命令。",
  "Choose an existing workspace directory for the command.": "请选择工作区中已有的目录来执行命令。",
  "The file changed during review. Read it and propose a fresh edit.": "文件在审阅期间发生了变化。请重新读取文件并提出修改。",
  "Choose an existing workspace folder or enter its full path.": "请选择已有的工作文件夹或输入其完整路径。",
  "Enter the workspace folder path in Settings.": "请在设置中输入工作文件夹路径。",
  "Could not open the folder picker. Enter the folder path in Settings.": "无法打开文件夹选择器。请在设置中输入路径。",
  "Could not read a local file. Check its location and permissions.": "无法读取本地文件。请检查文件位置和权限。",
  "Could not save a copy of this file.": "无法保存此文件的副本。",
  "This file is unavailable in this chat.": "无法从此聊天访问该文件。",
  "The saved file has been moved or removed.": "已保存的文件已被移走或删除。",
  "Use Show in folder to locate this file.": "请选择「在文件夹中显示」来定位文件。",
  "This worksheet is too large to preview. Save a copy to view it in full.": "工作表过大，无法预览。请保存副本查看完整内容。",
  "This workbook could not be previewed. Save a copy to open it in your spreadsheet app.": "无法预览此工作簿。请保存副本后用表格应用打开。",
  "Could not save to the workspace folder. Check its location and permissions.": "无法保存到工作文件夹，请检查位置和权限。",
  "Choose a workspace folder in Settings before exporting.": "请先在设置中选择导出文件夹。"
};

export function rateLimitDiagnostic(note = '', limit) {
  if (!limit && !/(?:^The rate limit was reached|\brate_limit_exceeded\b)/.test(note)) return null;
  const detail = note.replace(/ in organization org-[\w-]+/gi, '').replace(/\borg-[\w-]+/g, '[redacted]');
  if (/\brequest (?:is )?too large\b/i.test(note)) {
    return { temporary: false, detail: detail.replace(/^The rate limit was reached\. Wait a moment before retrying\.\n/, ''),
      summary: t('This request exceeds the token limit for a single request. Shorten the context or remove an attachment.',
      '单次请求的词元数已超出上限。请缩短上下文或移除附件。') };
  }
  const bucket = detail.match(/\(for limit ([\w.-]+)\)/i)?.[1] || detail.match(/rate limit reached for ([\w.-]+)/i)?.[1];
  const metric = limit?.metric || detail.match(/\b(TPM|RPM)\b/i)?.[1].toUpperCase();
  const subject = bucket || t('This API project', '当前 API 项目');
  const label = metric === 'TPM' ? t('Token-per-minute limit reached', '已达到每分钟词元上限')
    : metric === 'RPM' ? t('Requests-per-minute limit reached', '已达到每分钟请求数上限')
    : t('API rate limit reached', '已达到 API 速率上限');
  const amounts = detail.match(/\bLimit ([\d,.]+), Used ([\d,.]+), Requested ([\d,.]+)/i);
  const counts = amounts ? amounts.slice(1).map(n => Number(n.replaceAll(',', '')).toLocaleString(language())) : null;
  const usage = counts ? t('Limit {limit} · Used {used} · Requested {requested}', '上限 {limit} · 已用 {used} · 本次请求 {requested}',
    { limit: counts[0], used: counts[1], requested: counts[2] }) : '';
  const hint = /-(?:ultrafast|fast)$/.test(bucket || '') ? t('Try again shortly or choose Standard speed.', '请稍后再试，也可以切换到标准速度。')
    : t('Try again shortly.', '请稍后再试。');
  return { temporary: true, summary: [subject + ' · ' + label, usage, hint].filter(Boolean).join('\n'), detail };
}

export function systemMessage(value) {
  if (value === 'OpenAI could not complete the request: error') {
    return language() === 'zh-CN' ? '这次请求失败了。旧版 Relay 没有保存 API 返回的具体原因。'
      : 'This request failed. The earlier version of Relay did not save the API error details.';
  }
  if (language() !== 'zh-CN' || !value) return value;
  if (Object.hasOwn(messages, value)) return messages[value];
  if (value.includes('\n')) return value.split('\n').map(systemMessage).join('\n');
  return value.replace(/^Exited with code (-?\d+)\.$/, '退出码 $1。')
    .replace(/^Read (\d+) lines of (\d+)$/, '已读取 $1 行，共 $2 行')
    .replace(/^(\d+) matches \(limit reached\)$/, '找到 $1 处匹配（已达上限）')
    .replace(/^(\d+) matches$/, '找到 $1 处匹配')
    .replace(/^OpenAI could not complete the request: /, 'OpenAI 未能完成请求：');
}
