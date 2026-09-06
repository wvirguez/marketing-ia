import type { ContentItem } from "./campaign-workspace";

export type ContentFormat = ContentItem["format"];

export type ContentCopyBlocks = {
  script: string;
  caption: string;
  cta: string;
};

export type ContentDetailMeta = {
  slug: string;
  title: string;
  format: ContentFormat;
  status: string;
  objective: string;
  funnelStage: string;
  cta: string;
  channel: string;
  copy: ContentCopyBlocks;
};

export type ReelScene = {
  number: number;
  visual: string;
  onScreenText: string;
  narration: string;
};

export type ReelDetail = ContentDetailMeta & {
  kind: "reel";
  hook: string;
  scenes: ReelScene[];
  caption: string;
  hashtags: string[];
};

export type CarouselSlide = {
  number: number;
  title: string;
  body: string;
  visualObjective: string;
};

export type CarouselDetail = ContentDetailMeta & {
  kind: "carousel";
  slides: CarouselSlide[];
  finalCta: string;
};

export type StorySlideKind = "question" | "poll" | "tip" | "cta";

export type StorySlide = {
  number: number;
  kind: StorySlideKind;
  title: string;
  body?: string;
  pollOptions?: string[];
};

export type StoryDetail = ContentDetailMeta & {
  kind: "story";
  slides: StorySlide[];
};

export type ContentDetailData = ReelDetail | CarouselDetail | StoryDetail;
