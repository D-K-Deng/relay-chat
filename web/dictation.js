import { systemMessage } from './system-messages.js';
import { t } from './i18n.js';
export function installDictation({ button, status, draft, configured, toast, onTranscribed }) {
  const supported = !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  const title = () => supported ? t("Hold to dictate · release to transcribe", "按住说话，松开转写") : t("Microphone recording is unavailable in this browser", "当前浏览器无法使用麦克风录音");
  button.title = title();
  button.disabled = !supported;
  let current = null;

  function reset(session) {
    clearTimeout(session.timer);
    session.stream?.getTracks().forEach(track => track.stop());
    if (current !== session) return;
    current = null;
    button.removeAttribute('data-recording');
    button.setAttribute('aria-pressed', 'false');
    button.title = title();
    status.replaceChildren();
  }

  function cancel() {
    if (!current) return;
    const session = current;
    session.upload.abort();
    if (session.recorder?.state === 'recording') session.recorder.stop();
    reset(session);
  }

  function showStatus(message, action, label) {
    status.textContent = message;
    if (action) {
      const control = document.createElement('button');
      control.type = 'button';
      control.textContent = label;
      control.onclick = action;
      status.append(' ', control);
    }
  }

  async function transcribe(session) {
    showStatus(t("Transcribing…", "正在转写…"), cancel, t("Cancel", "取消"));
    try {
      const response = await fetch('/api/transcribe', {
        method: 'POST', headers: { 'Content-Type': session.clip.type }, body: session.clip, signal: session.upload.signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(systemMessage(result.error) || t("Transcription failed. Please try again.", "转写失败，请重试。"));
      if (current !== session) return;
      const pos = draft.selectionEnd;
      const text = result.text;
      const prefix = /[A-Za-z0-9]$/.test(draft.value.slice(0, pos)) && /^[A-Za-z0-9]/.test(text) ? ' ' : '';
      const suffix = /[A-Za-z0-9]$/.test(text) && /^[A-Za-z0-9]/.test(draft.value.slice(pos)) ? ' ' : '';
      draft.setRangeText(prefix + text + suffix, pos, pos, 'end');
      draft.dispatchEvent(new Event('input', { bubbles: true }));
      reset(session);
      draft.focus({ preventScroll: true });
      onTranscribed?.();
    } catch (error) {
      if (current !== session) return;
      toast(error.message);
      showStatus(t("Recording ready.", "录音已就绪。"), () => transcribe(session), t("Retry transcription", "重新转写"));
      const discard = document.createElement('button');
      discard.type = 'button';
      discard.textContent = t("Discard", "丢弃");
      discard.onclick = cancel;
      status.append(' ', discard);
    }
  }

  function release() {
    if (!current || current.clip) return;
    if (current.recorder?.state === 'recording') {
      clearTimeout(current.timer);
      current.recorder.stop();
    } else if (!current.recorder) {
      current.released = true;
      showStatus(t("Waiting for microphone access…", "正在等待麦克风权限…"), cancel, t("Cancel", "取消"));
    }
  }

  async function start() {
    if (!supported || draft.readOnly || draft.disabled) return;
    if (!configured()) return toast(t("Connect your API key to use dictation.", "请先连接 API 密钥，再使用听写。"));
    cancel();
    const session = { upload: new AbortController() };
    current = session;
    showStatus(t("Opening microphone…", "正在打开麦克风…"));
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (current !== session) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      session.stream = stream;
      if (session.released) {
        reset(session);
        toast(t("Microphone ready. Hold the microphone button while you speak.", "麦克风已就绪，按住麦克风按钮说话。"));
        return;
      }
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error(t("This browser cannot record a supported audio format.", "当前浏览器无法录制受支持的音频格式。"));
      const recorder = new MediaRecorder(stream, { mimeType });
      session.recorder = recorder;
      const chunks = [];
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => {
        if (current !== session) return;
        cancel();
        toast(t("The microphone stopped recording. Check your audio input and try again.", "麦克风已停止录音。请检查音频输入后重试。"));
      };
      recorder.onstop = () => {
        stream.getTracks().forEach(track => track.stop());
        if (current !== session) return;
        session.clip = new Blob(chunks, { type: mimeType.split(';')[0] });
        button.removeAttribute('data-recording');
        button.setAttribute('aria-pressed', 'false');
        if (session.clip.size && session.clip.size <= 25_000_000) void transcribe(session);
        else {
          reset(session);
          toast(t("Record a short phrase, then release to transcribe it.", "按住说一小段话，松开即可转写。"));
        }
      };
      recorder.start();
      button.dataset.recording = 'true';
      button.setAttribute('aria-pressed', 'true');
      showStatus(t("Listening… Release to transcribe · Esc to cancel", "正在聆听… 松开转写 · Esc 取消"));
      session.timer = setTimeout(release, 120_000);
    } catch (error) {
      if (current !== session) return;
      reset(session);
      toast(error.name === 'NotAllowedError' ? t("Allow microphone access in your system or browser settings to dictate.", "请在系统或浏览器设置中允许使用麦克风。")
        : error.name === 'NotFoundError' ? t("Connect a microphone to use dictation.", "请先连接麦克风。") : error.message);
    }
  }

  button.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    void start();
  });
  button.addEventListener('pointerup', release);
  button.addEventListener('pointercancel', cancel);
  button.addEventListener('keydown', event => {
    if (![' ', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    if (!event.repeat) void start();
  });
  button.addEventListener('keyup', event => {
    if ([' ', 'Enter'].includes(event.key)) {
      event.preventDefault();
      release();
    }
  });
  // Assistive technologies can activate a button without pointer or key events.
  button.addEventListener('click', event => {
    if (event.detail !== 0) return;
    if (current?.recorder?.state === 'recording') release();
    else void start();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && current) cancel();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && current && !current.clip) cancel();
  });
  window.addEventListener('pagehide', cancel);
  window.addEventListener('relay-languagechange', () => { if (!current) button.title = title(); });
  return { cancel, supported };
}
