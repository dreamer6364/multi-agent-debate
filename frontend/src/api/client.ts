import axios from "axios";
import type {
  DebateDetail,
  DebateFormat,
  MeInfo,
  SpeechPage,
  TopicItem,
} from "./types";

export const api = axios.create({ baseURL: "/api", withCredentials: true, timeout: 30000 });

// 401 统一跳登录页（前端不碰任何密钥，CONSTRAINTS B1）；
// 带 skipAuthRedirect 标记的请求（如开始页的 me 探测）静默处理，不跳转
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const skip = error.config?.skipAuthRedirect === true;
    if (
      error.response?.status === 401 &&
      !skip &&
      !window.location.pathname.startsWith("/login")
    ) {
      window.location.href = "/login";
    }
    return Promise.reject(error);
  },
);

export async function register(username: string, password: string): Promise<void> {
  await api.post("/auth/register", { username, password });
}

export async function login(username: string, password: string): Promise<void> {
  await api.post("/auth/login", { username, password });
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

export async function me(): Promise<MeInfo | null> {
  // 静默探测：401 返回 null（开始页访客态），不触发全局跳转
  try {
    return (await api.get<MeInfo>("/auth/me", { skipAuthRedirect: true } as never)).data;
  } catch {
    return null;
  }
}

export async function listTopics(): Promise<TopicItem[]> {
  return (await api.get<{ items: TopicItem[] }>("/topics")).data.items;
}

export async function createTopic(body: {
  title: string;
  pro_points: string;
  con_points: string;
}): Promise<void> {
  await api.post("/topics", body);
}

export async function deleteTopic(id: number, force = false): Promise<void> {
  await api.delete(`/topics/${id}`, { params: force ? { force: true } : undefined });
}

export async function createDebate(body: {
  topic_id?: number;
  custom_topic?: { title: string; pro_points: string; con_points: string };
  format: DebateFormat;
  judges: number;
}): Promise<{ id: number; status: string }> {
  return (await api.post("/debates", body)).data;
}

export async function getDebate(id: number | string): Promise<DebateDetail> {
  return (await api.get<DebateDetail>(`/debates/${id}`)).data;
}

export async function getDebates(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: Array<{ id: number; topic_title: string; format: string; status: string; winner: string | null; created_at: string }>; total: number }> {
  return (await api.get("/debates", { params })).data;
}

/** 单场补拉的分页上限保护：后端异常 has_more 恒 true 时也不至于无限请求 */
const MAX_SPEECH_PAGES = 20;

/**
 * FE-2（ISS-012）：后端单页上限 500 且返回 has_more，按游标循环拉全。
 * 此前固定拉一页，超过 500 条发言的归档回看会静默截断。
 */
export async function getSpeeches(id: number | string, afterSeq = 0): Promise<SpeechPage> {
  const items: SpeechPage["items"] = [];
  let cursor = afterSeq;
  for (let page = 0; page < MAX_SPEECH_PAGES; page += 1) {
    const chunk = (await api.get<SpeechPage>(`/debates/${id}/speeches`, {
      params: { after_seq: cursor, limit: 500 },
    })).data;
    items.push(...chunk.items);
    if (!chunk.has_more || chunk.items.length === 0) {
      return { items, has_more: false };
    }
    const last = chunk.items[chunk.items.length - 1].seq;
    if (last <= cursor) break; // 游标未推进：终止而非死循环
    cursor = last;
  }
  return { items, has_more: true };
}

/** v2.5：逐轮图快照（归档回看时按版本翻看历史图） */

export async function stopDebate(id: number | string): Promise<void> {
  await api.post(`/debates/${id}/stop`);
}

/** 删除单场历史归档（连同发言与评分卡）；进行中的场次后端会拒绝，需先终止。 */
export async function deleteDebate(id: number | string): Promise<void> {
  await api.delete(`/debates/${id}`);
}

/** 清空历史归档：删除本人全部已结束/失败/停止的场次，返回删除条数。 */
export async function clearArchivedDebates(): Promise<{ deleted: number }> {
  return (await api.delete<{ deleted: number }>("/debates")).data;
}
