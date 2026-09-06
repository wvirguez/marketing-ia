export type Campaign = {
  id: string;
  name: string;
  category: string;
  status: "En preparación" | "Activa" | "Borrador";
  progress: number;
  date: string;
  dateLabel: string;
  initials: string;
  tone: "blue" | "mint" | "violet";
  workspaceHref?: "/campaigns/demo";
};
