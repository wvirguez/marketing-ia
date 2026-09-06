import { storyKindLabel } from "@/lib/content-demo-data";
import type { CarouselDetail, ContentDetailData, ReelDetail, StoryDetail } from "@/types/content-detail";

export function ContentScript({ detail }: { detail: ContentDetailData }) {
  if (detail.kind === "reel") return <ReelScript detail={detail} />;
  if (detail.kind === "carousel") return <CarouselScript detail={detail} />;
  return <StoryScript detail={detail} />;
}

function ReelScript({ detail }: { detail: ReelDetail }) {
  return (
    <div className="content-script">
      <section className="panel content-hook">
        <span className="content-hook-label">Hook</span>
        <p>{detail.hook}</p>
      </section>
      <section className="panel content-panel">
        <h2>Guion por escenas</h2>
        <ol className="content-scene-list">
          {detail.scenes.map(scene => (
            <li key={scene.number} className="content-scene-card">
              <span className="content-scene-number">Escena {scene.number}</span>
              <dl>
                <div><dt>Dirección visual</dt><dd>{scene.visual}</dd></div>
                <div><dt>Texto en pantalla</dt><dd>{scene.onScreenText}</dd></div>
                <div><dt>Voz / narración</dt><dd>{scene.narration}</dd></div>
              </dl>
            </li>
          ))}
        </ol>
      </section>
      <section className="panel content-panel">
        <h2>Caption</h2>
        <p className="content-caption-text">{detail.caption}</p>
        <div className="content-hashtags">{detail.hashtags.map(tag => <span key={tag} className="content-hashtag">{tag}</span>)}</div>
      </section>
      <section className="panel content-panel content-cta-panel">
        <h2>CTA</h2>
        <p>{detail.copy.cta}</p>
      </section>
    </div>
  );
}

function CarouselScript({ detail }: { detail: CarouselDetail }) {
  return (
    <div className="content-script">
      <section className="panel content-panel">
        <h2>Diapositivas</h2>
        <ol className="content-scene-list">
          {detail.slides.map(slide => (
            <li key={slide.number} className="content-scene-card">
              <span className="content-scene-number">{slide.number === 1 ? "Portada" : `Diapositiva ${slide.number}`}</span>
              <h3>{slide.title}</h3>
              <dl>
                <div><dt>Copy</dt><dd>{slide.body}</dd></div>
                <div><dt>Objetivo visual</dt><dd>{slide.visualObjective}</dd></div>
              </dl>
            </li>
          ))}
        </ol>
      </section>
      <section className="panel content-panel content-cta-panel">
        <h2>CTA final</h2>
        <p>{detail.finalCta}</p>
      </section>
    </div>
  );
}

function StoryScript({ detail }: { detail: StoryDetail }) {
  return (
    <div className="content-script">
      <section className="panel content-panel">
        <h2>Secuencia de stories</h2>
        <ol className="content-scene-list">
          {detail.slides.map(slide => (
            <li key={slide.number} className="content-scene-card">
              <span className="content-scene-number">Story {slide.number} · {storyKindLabel[slide.kind]}</span>
              <h3>{slide.title}</h3>
              {slide.body && <p className="content-story-body">{slide.body}</p>}
              {slide.pollOptions && (
                <ul className="content-poll-list">
                  {slide.pollOptions.map(option => <li key={option}>{option}</li>)}
                </ul>
              )}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
