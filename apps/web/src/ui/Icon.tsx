import { ICONS } from "./icons";

export function Icon({ name, className = "", title }: { name: string; className?: string; title?: string }) {
  return (
    <svg className={`ic ${className}`} viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title ? <title>{title}</title> : null}
      <g dangerouslySetInnerHTML={{ __html: ICONS[name] || "" }} />
    </svg>
  );
}
