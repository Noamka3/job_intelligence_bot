// A company is recognizable at a glance by a colour that is always the
// same for that company: the hue is derived from the name, so no
// palette has to be maintained and two companies rarely collide.
function hueFor(name: string): number {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.codePointAt(0)!) >>> 0;
  return hash % 360;
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

export function CompanyTag({ name, large = false }: { name: string; large?: boolean }) {
  const hue = hueFor(name);
  return (
    <span
      className={`company${large ? " company--large" : ""}`}
      style={{ "--hue": hue } as React.CSSProperties}
      title={name}
    >
      <span className="company__avatar" aria-hidden="true">
        {initials(name)}
      </span>
      <span className="company__name bidi">{name}</span>
    </span>
  );
}
