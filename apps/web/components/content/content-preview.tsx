"use client";

import { useState } from "react";
import { Icon } from "@/components/ui/icon";
import { storyKindLabel } from "@/lib/content-demo-data";
import type { CarouselDetail, ContentDetailData, ReelDetail, StoryDetail } from "@/types/content-detail";

export function ContentPreview({ detail }: { detail: ContentDetailData }) {
  if (detail.kind === "reel") return <ReelPreview detail={detail} />;
  if (detail.kind === "carousel") return <CarouselPreview detail={detail} />;
  return <StoryPreview detail={detail} />;
}

function ReelPreview({ detail }: { detail: ReelDetail }) {
  return (
    <figure className="content-preview-frame ratio-9-16 content-preview-navy">
      <div className="content-preview-inner" aria-hidden="true">
        <div className="content-preview-statusbar"><span /><span /><span /></div>
        <span className="content-preview-brand">impulso.</span>
        <div className="content-preview-orbit" />
        <p className="content-preview-hook">{detail.hook}</p>
        <span className="content-preview-cta-pill">{detail.cta}</span>
      </div>
      <figcaption className="sr-only">Vista previa del reel «{detail.title}»: {detail.hook}</figcaption>
    </figure>
  );
}

function CarouselPreview({ detail }: { detail: CarouselDetail }) {
  const [index, setIndex] = useState(0);
  const slide = detail.slides[index];
  return (
    <div className="content-preview-stack-inner">
      <figure className="content-preview-frame ratio-4-5 content-preview-cyan">
        <div className="content-preview-inner" aria-hidden="true">
          <span className="content-preview-brand">impulso.</span>
          <div className="content-preview-orbit" />
          <div className="content-preview-slide-copy">
            <span className="content-preview-slide-index">{index + 1}/{detail.slides.length}</span>
            <h3>{slide.title}</h3>
            <p>{slide.body}</p>
          </div>
        </div>
        <figcaption className="sr-only">Vista previa de la diapositiva {index + 1} de {detail.slides.length} del carrusel «{detail.title}»: {slide.title}. {slide.body}</figcaption>
      </figure>
      <div className="content-preview-nav" role="group" aria-label="Navegar diapositivas del carrusel">
        <button type="button" className="icon-button" onClick={() => setIndex(current => Math.max(0, current - 1))} disabled={index === 0} aria-label="Diapositiva anterior">
          <Icon name="chevron" size={14} style={{ transform: "rotate(180deg)" }} />
        </button>
        <div className="content-preview-dots">
          {detail.slides.map((item, itemIndex) => (
            <button key={item.number} type="button" className={`content-preview-dot${itemIndex === index ? " is-active" : ""}`} aria-label={`Ir a la diapositiva ${itemIndex + 1}: ${item.title}`} aria-current={itemIndex === index} onClick={() => setIndex(itemIndex)} />
          ))}
        </div>
        <button type="button" className="icon-button" onClick={() => setIndex(current => Math.min(detail.slides.length - 1, current + 1))} disabled={index === detail.slides.length - 1} aria-label="Siguiente diapositiva">
          <Icon name="chevron" size={14} />
        </button>
      </div>
      <p className="content-preview-final-cta"><strong>CTA final:</strong> {detail.finalCta}</p>
    </div>
  );
}

function StoryPreview({ detail }: { detail: StoryDetail }) {
  const [index, setIndex] = useState(0);
  const slide = detail.slides[index];
  return (
    <div className="content-preview-stack-inner">
      <figure className="content-preview-frame ratio-9-16 content-preview-navy">
        <div className="content-preview-inner" aria-hidden="true">
          <div className="content-preview-story-progress">{detail.slides.map((item, itemIndex) => <span key={item.number} className={itemIndex <= index ? "is-filled" : ""} />)}</div>
          <span className="content-preview-brand">impulso.</span>
          <div className="content-preview-slide-copy">
            <span className="content-preview-slide-index">{storyKindLabel[slide.kind]}</span>
            <h3>{slide.title}</h3>
            {slide.body && <p>{slide.body}</p>}
            {slide.pollOptions && <div className="content-poll-options">{slide.pollOptions.map(option => <span key={option} className="content-poll-option">{option}</span>)}</div>}
          </div>
        </div>
        <figcaption className="sr-only">Vista previa de la story {index + 1} de {detail.slides.length} («{storyKindLabel[slide.kind]}») del contenido «{detail.title}»: {slide.title}{slide.body ? `. ${slide.body}` : ""}{slide.pollOptions ? `. Opciones de encuesta: ${slide.pollOptions.join(", ")}` : ""}</figcaption>
      </figure>
      <div className="content-preview-dots" role="group" aria-label="Navegar stories">
        {detail.slides.map((item, itemIndex) => (
          <button key={item.number} type="button" className={`content-preview-dot${itemIndex === index ? " is-active" : ""}`} aria-label={`Ir a la story ${itemIndex + 1}: ${item.title}`} aria-current={itemIndex === index} onClick={() => setIndex(itemIndex)} />
        ))}
      </div>
    </div>
  );
}
