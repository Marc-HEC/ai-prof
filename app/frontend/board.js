// Tableau blanc du prof. Reçoit les blocs <tableau> écrits par le LLM et les affiche
// au fil de l'eau : lignes manuscrites (texte, formules) ou schémas Mermaid au trait.
// Tout est local : Mermaid vient de node_modules, aucune ressource en ligne.

let mermaidReady = null;
function loadMermaid() {
  mermaidReady ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      look: "handDrawn",
      fontFamily: '"Ink Free", "Segoe Print", "Comic Sans MS", cursive',
    });
    return mermaid;
  });
  return mermaidReady;
}

export class Board {
  constructor(root) {
    this.root = root;
    this.surface = root.querySelector(".board-surface");
    this.sources = root.querySelector(".board-sources");
    this.count = 0;
    root.querySelector(".board-clear").onclick = () => this.clear();
  }

  show() { this.root.hidden = false; document.body.classList.add("has-board"); }

  clear() {
    this.surface.replaceChildren();
    this.sources.replaceChildren();
    this.sources.hidden = true;
  }

  handle(m) {
    this.show();
    if (m.type === "board" && m.op === "notes") return this.notes(m.lines);
    if (m.type === "board" && m.op === "schema") return this.schema(m.mermaid);
    if (m.type === "search") return this.searching(m.query);
    if (m.type === "sources") return this.listSources(m.query, m.items);
  }

  block(kind) {
    const el = document.createElement("div");
    el.className = `board-item ${kind}`;
    this.surface.append(el);
    el.scrollIntoView({ block: "end", behavior: "smooth" });
    return el;
  }

  // Chaque ligne apparaît comme si elle était écrite au feutre, l'une après l'autre.
  notes(lines) {
    const el = this.block("notes");
    lines.forEach((text, i) => {
      const line = document.createElement("p");
      line.textContent = text;
      line.style.animationDelay = `${i * 0.45}s`;
      el.append(line);
    });
  }

  async schema(code) {
    const el = this.block("schema");
    try {
      const mermaid = await loadMermaid();
      const { svg } = await mermaid.render(`schema-${++this.count}`, code);
      el.innerHTML = svg;
    } catch (err) {
      console.warn("schéma illisible", err);
      el.remove();
      this.notes(code.split("\n").map((l) => l.trim()).filter(Boolean)); // on montre au moins le texte
    }
  }

  searching(query) {
    this.sources.hidden = false;
    this.sources.replaceChildren(Object.assign(document.createElement("p"), {
      className: "board-searching", textContent: `Recherche : ${query}…` }));
  }

  listSources(query, items) {
    this.sources.hidden = false;
    const title = Object.assign(document.createElement("p"), { className: "board-sources-title", textContent: `Sources pour « ${query} »` });
    const list = document.createElement("ol");
    for (const s of items) {
      const li = document.createElement("li");
      const a = Object.assign(document.createElement("a"), { href: s.url, textContent: s.titre || s.url, target: "_blank", rel: "noreferrer" });
      li.append(a);
      list.append(li);
    }
    this.sources.replaceChildren(title, list);
  }
}
