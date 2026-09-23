// Tableau : le prof y ecrit (Markdown + LaTeX), l'eleve y dessine et peut
// montrer son dessin au prof (modele vision).

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Repli si marked n'a pas pu etre charge : titres, gras, italique, listes.
function miniMarkdown(src) {
  const inline = (s) => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\W)\*(.+?)\*(?=\W|$)/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const line of src.split("\n")) {
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const li = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.*)$/);
    if (!li && list) { out.push(`</${list}>`); list = null; }
    if (h) out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`);
    else if (li) {
      const kind = /^\s*\d/.test(line) ? "ol" : "ul";
      if (!list) { list = kind; out.push(`<${kind}>`); }
      out.push(`<li>${inline(li[1])}</li>`);
    } else if (/^\u0000\d+\u0000$/.test(line.trim())) out.push(line.trim()); // formule seule
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join("");
}

// Les formules sont extraites avant le Markdown, sinon marked mange les "_" et
// les "\\" du LaTeX. Elles sont rendues par KaTeX puis reinserees.
export function renderMarkdownMath(src) {
  const math = [];
  const stash = (tex, display) => {
    math.push({ tex, display });
    return `\u0000${math.length - 1}\u0000`;
  };
  let text = src
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => stash(t, true))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => stash(t, false))
    .replace(/(^|[^\\$])\$([^\s$](?:[^$\n]*?[^\s$])?)\$/g, (_, pre, t) => pre + stash(t, false));

  let html;
  if (window.marked && window.marked.parse) {
    html = window.marked.parse(text, { breaks: true });
  } else {
    html = miniMarkdown(text);
  }

  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const { tex, display } = math[Number(i)];
    if (window.katex) {
      try {
        return window.katex.renderToString(tex, { displayMode: display, throwOnError: false });
      } catch (_) {}
    }
    const code = escapeHtml(tex);
    return display ? `<pre class="tex">${code}</pre>` : `<code class="tex">${code}</code>`;
  });
}

export class Board {
  constructor({ content, canvas, surface }) {
    this.content = content;
    this.canvas = canvas;
    this.surface = surface;
    this.ctx = canvas.getContext("2d");
    this.tool = "none";
    this.color = "#1d4ed8";
    this.dirty = false;
    this._setupDrawing();
  }

  add(markdown) {
    const card = document.createElement("article");
    card.className = "board-card";
    card.innerHTML = renderMarkdownMath(markdown);
    this.content.querySelector(".board-empty")?.remove();
    this.content.appendChild(card);
    card.scrollIntoView({ behavior: "smooth", block: "end" });
    return card;
  }

  clearWriting() {
    this.content.innerHTML = '<p class="board-empty">Le tableau est vide. Demande un cours, un exercice, ou dessine.</p>';
  }

  setTool(tool) {
    this.tool = tool;
    this.surface.dataset.tool = tool;
  }

  clearDrawing() {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.dirty = false;
  }

  // Image envoyee au modele vision : le dessin sur fond blanc, avec le texte
  // du tableau en dessous n'est pas necessaire, le LLM l'a deja dans l'historique.
  snapshot() {
    if (!this.dirty) return null;
    const out = document.createElement("canvas");
    const scale = Math.min(1, 1280 / this.canvas.width);
    out.width = Math.round(this.canvas.width * scale);
    out.height = Math.round(this.canvas.height * scale);
    const g = out.getContext("2d");
    g.fillStyle = "#fff";
    g.fillRect(0, 0, out.width, out.height);
    g.drawImage(this.canvas, 0, 0, out.width, out.height);
    return out.toDataURL("image/jpeg", 0.85);
  }

  _setupDrawing() {
    const c = this.canvas;
    const resize = () => {
      const r = window.devicePixelRatio || 1;
      const { width, height } = this.surface.getBoundingClientRect();
      if (!width || !height) return;
      const prev = this.dirty ? c.toDataURL() : null;
      c.width = Math.round(width * r);
      c.height = Math.round(height * r);
      c.style.width = width + "px";
      c.style.height = height + "px";
      if (prev) {
        const img = new Image();
        img.onload = () => this.ctx.drawImage(img, 0, 0, c.width, c.height);
        img.src = prev;
      }
    };
    new ResizeObserver(resize).observe(this.surface);
    resize();

    let drawing = false, last = null;
    const pos = (e) => {
      const b = c.getBoundingClientRect();
      const r = c.width / b.width;
      return [(e.clientX - b.left) * r, (e.clientY - b.top) * r];
    };
    c.addEventListener("pointerdown", (e) => {
      if (this.tool === "none") return;
      drawing = true; last = pos(e);
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!drawing) return;
      const p = pos(e);
      const g = this.ctx;
      const r = window.devicePixelRatio || 1;
      g.globalCompositeOperation = this.tool === "eraser" ? "destination-out" : "source-over";
      g.strokeStyle = this.color;
      g.lineWidth = (this.tool === "eraser" ? 22 : 3) * r * (e.pressure ? 0.6 + e.pressure : 1);
      g.lineCap = "round"; g.lineJoin = "round";
      g.beginPath(); g.moveTo(last[0], last[1]); g.lineTo(p[0], p[1]); g.stroke();
      last = p;
      if (this.tool === "pen") this.dirty = true;
    });
    const end = () => { drawing = false; };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
  }
}
