export interface Project {
  id: string;
  name: string;
  status: string;
  duration: number | null;
  last_error: string | null;
  created_at: string;
}

export interface AsrModel {
  value: string;
  label: string;
  desc: string;
}

export interface AppSettings {
  asr_model: string;
  asr_model_label: string;
  asr_model_desc: string;
  whisper_model_size: string;
  runninghub_api_key: string;
  runninghub_workflow_id: string;
  available_models: AsrModel[];
}

export interface ModelStatus {
  name: string;
  label: string;
  size_gb: number;
  downloaded: boolean;
  size_downloaded_gb: number;
  path: string;
  engine?: string;
}

export interface ModelStatusResponse {
  models: ModelStatus[];
  current_model: string;
}

export interface Speaker {
  id: string;
  name: string;
  reference_audio: string | null;
}

export interface Sentence {
  id: string;
  speaker_id: string | null;
  speaker_name: string;
  text: string;
  start_time: number;
  end_time: number;
  emotion_happy: number;
  emotion_angry: number;
  emotion_sad: number;
  emotion_fear: number;
  emotion_hate: number;
  emotion_low: number;
  emotion_surprise: number;
  emotion_neutral: number;
  tts_status: 'pending' | 'generating' | 'done' | 'failed';
  generated_audio: string | null;
  is_deleted: boolean;
  sort_order: number;
}

export interface ProjectDetail extends Project {
  speakers: Speaker[];
  sentences: Sentence[];
}

export const EMOTION_LABELS: Record<string, string> = {
  happy: '开心',
  angry: '愤怒',
  sad: '悲伤',
  fear: '恐惧',
  hate: '厌恶',
  low: '低落',
  surprise: '惊讶',
  neutral: '中性',
};

export const EMOTION_KEYS = ['happy', 'angry', 'sad', 'fear', 'hate', 'low', 'surprise', 'neutral'];
