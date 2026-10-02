/* Site novo: lê dados/<publico>.json e desenha a página.

   - O público vem de <body data-publico="...">. Cada página só busca o SEU arquivo:
     quem vê o quê é decidido no dado (exportar.py) e na permissão do arquivo.
   - Nada é calculado aqui além de somas do período escolhido: os números vêm prontos.
   - Texto vindo do dado (assunto, solicitante, cliente) entra SEMPRE por textContent.
     Não use innerHTML neste arquivo.
   - Nenhuma cor aqui: os gráficos leem as variáveis do tema (tema-sino.css).          */
(function () {
  "use strict";

  var PUBLICO = document.body.getAttribute("data-publico");
  var BUSCA_SEGUNDOS = 60;
  // idade do dado (minutos desde a última coleta) a partir da qual o carimbo muda
  var FRESCOR_ATENCAO_MIN = 15;
  var FRESCOR_ALERTA_MIN = 90;
  var LICENCAS_ALERTA_HORAS = 24;
  var PERIODOS = [7, 14, 30];
  var MAX_CLIENTES = 10;

  var estado = { dados: null, assinatura: null, dias: lerPreferencia(), falha: null, graficos: [] };

  /* ---- utilidades -------------------------------------------------------- */
  function el(tag, atributos) {
    var no = document.createElement(tag);
    Object.keys(atributos || {}).forEach(function (k) {
      var v = atributos[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "texto") no.textContent = v;
      else if (k === "estilo") Object.keys(v).forEach(function (p) { no.style.setProperty(p, v[p]); });
      else no.setAttribute(k, v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var filho = arguments[i];
      if (filho === null || filho === undefined || filho === false) continue;
      no.appendChild(typeof filho === "object" ? filho : document.createTextNode(String(filho)));
    }
    return no;
  }
  function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }
  function int(v) { return num(v) === null ? "—" : Math.round(v).toLocaleString("pt-BR"); }
  function pct(parte, todo) { return num(parte) === null || !todo ? "—" : Math.round(100 * parte / todo) + "%"; }
  function obj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : {}; }
  function lista(v) { return Array.isArray(v) ? v : []; }
  function plural(n, um, varios) { return int(n) + " " + (n === 1 ? um : varios); }
  function data(iso) { var d = new Date(iso || ""); return isNaN(d) ? null : d; }
  function dois(n) { return (n < 10 ? "0" : "") + n; }
  function hora(iso) { var d = data(iso); return d ? dois(d.getHours()) + ":" + dois(d.getMinutes()) : "—"; }
  function diaMes(iso) { var p = String(iso || "").slice(0, 10).split("-"); return p.length === 3 ? p[2] + "/" + p[1] : "—"; }
  function diaHora(iso) { return data(iso) ? diaMes(iso) + " às " + hora(iso) : "—"; }
  function minutosDesde(iso) { var d = data(iso); return d ? Math.max(0, Math.floor((Date.now() - d) / 60000)) : null; }
  function haQuanto(min) {
    if (min === null) return "sem registro";
    if (min < 1) return "agora mesmo";
    if (min < 60) return "há " + min + " min";
    if (min < 48 * 60) return "há " + Math.floor(min / 60) + " h" + (min % 60 && min < 600 ? " " + (min % 60) + " min" : "");
    return "há " + Math.floor(min / 1440) + " dias";
  }
  function duracao(horas) {
    if (num(horas) === null) return "—";
    if (horas < 1) return "menos de 1 h";
    if (horas < 48) return Math.round(horas).toLocaleString("pt-BR") + " h";
    return (horas / 24).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " dias";
  }
  function cor(nome) { return getComputedStyle(document.documentElement).getPropertyValue(nome).trim(); }
  function lerPreferencia() {
    try { var v = parseInt(localStorage.getItem("periodo-dias"), 10); return PERIODOS.indexOf(v) >= 0 ? v : 14; }
    catch (e) { return 14; }
  }
  function guardarPreferencia(v) { try { localStorage.setItem("periodo-dias", String(v)); } catch (e) { /* sem armazenamento */ } }

  /* ---- componentes ------------------------------------------------------- */
  function cartao(titulo, definicao, classe) {
    var c = el("section", { "class": "cartao " + (classe || "") },
      el("header", {}, el("h3", { "class": "cartao-titulo", texto: titulo }),
        definicao ? el("p", { "class": "cartao-def", texto: definicao }) : null));
    for (var i = 3; i < arguments.length; i++) if (arguments[i]) c.appendChild(arguments[i]);
    return c;
  }
  function kpi(rotulo, valor, sub, unidade) {
    return el("div", { "class": "kpi" },
      el("div", { "class": "kpi-rotulo", texto: rotulo }),
      el("div", { "class": "kpi-valor" }, valor, unidade ? el("small", { texto: unidade }) : null),
      sub ? el("div", { "class": "kpi-sub", texto: sub }) : null);
  }
  function vazio(texto) { return el("p", { "class": "vazio", texto: texto }); }
  function faixa(id, titulo, sub, extra) {
    return el("section", { "class": "faixa", id: id },
      el("div", { "class": "faixa-cabeca" },
        el("div", {}, el("h2", { "class": "faixa-titulo", texto: titulo }), sub ? el("p", { "class": "faixa-sub", texto: sub }) : null),
        extra || null));
  }
  function legenda(itens) {
    return el.apply(null, ["div", { "class": "legenda" }].concat(itens.map(function (i) {
      return el("span", {}, el("i", { estilo: { "--cor": "var(" + i[1] + ")" } }), i[0]);
    })));
  }
  /* linhas: [{rotulo, partes:[{valor, cor, nome}], valor, nota, selo}] */
  function barras(linhas, maximo) {
    var teto = maximo || Math.max.apply(null, linhas.map(function (l) { return l.valor || 0; }).concat([1]));
    return el.apply(null, ["div", { "class": "barras", role: "list" }].concat(linhas.map(function (l) {
      var trilho = el("div", { "class": "barra-trilho" });
      l.partes.forEach(function (p) {
        if (!p.valor) return;
        trilho.appendChild(el("span", { title: p.nome + ": " + int(p.valor),
          estilo: { width: (100 * p.valor / teto) + "%", "--cor": "var(" + p.cor + ")" } }));
      });
      return el("div", { "class": "barra", role: "listitem" },
        el("div", { "class": "barra-rotulo", title: l.rotulo }, l.rotulo, l.selo ? el("span", { "class": "selo", texto: l.selo }) : null),
        trilho,
        el("div", { "class": "barra-valor" }, el("b", { texto: int(l.valor) }), l.nota ? el("small", { texto: l.nota }) : null));
    })));
  }
  function tabela(colunas, linhas, classe) {
    var cabeca = el("tr");
    colunas.forEach(function (c) { cabeca.appendChild(el("th", { "class": c.classe || null, scope: "col", texto: c.titulo })); });
    var corpo = el("tbody");
    linhas.forEach(function (l) {
      var tr = el("tr");
      colunas.forEach(function (c) {
        var v = c.valor(l);
        var td = el("td", { "class": c.classe || null });
        if (v instanceof Node) td.appendChild(v);
        else td.textContent = v === null || v === undefined || v === "" || typeof v === "object" ? "—" : v;
        tr.appendChild(td);
      });
      corpo.appendChild(tr);
    });
    return el("div", { "class": "rolagem" }, el("table", { "class": classe || null }, el("thead", {}, cabeca), corpo));
  }

  /* ---- AGORA (estoque) --------------------------------------------------- */
  var FAIXAS = [["ate_7_dias", "até 7 dias", "--seq-1"], ["de_8_a_30_dias", "8 a 30 dias", "--seq-2"],
    ["de_31_a_90_dias", "31 a 90 dias", "--seq-3"], ["mais_de_90_dias", "mais de 90 dias", "--seq-4"]];

  function blocoIdade(estoque) {
    var idade = obj(estoque.idade), total = estoque.total_abertos || 0;
    var pilha = el("div", { "class": "pilha", role: "img",
      "aria-label": FAIXAS.map(function (f) { return f[1] + ": " + int(idade[f[0]]); }).join("; ") });
    var itens = el("div", { "class": "pilha-legenda" });
    FAIXAS.forEach(function (f) {
      var n = idade[f[0]] || 0;
      if (n) pilha.appendChild(el("span", { title: f[1] + ": " + int(n) + " (" + pct(n, total) + ")",
        estilo: { flex: n + " 0 0", background: "var(" + f[2] + ")" } }));
      itens.appendChild(el("div", { "class": "pilha-item", estilo: { "--cor": "var(" + f[2] + ")" } },
        el("b", { texto: int(n) }), el("span", { texto: f[1] + " · " + pct(n, total) })));
    });
    return cartao("Idade da fila", "Há quanto tempo cada chamado em aberto foi criado. É a medida de urgência: " +
      "este sistema não tem prazo (SLA). Fonte: Milldesk, data de abertura.", "",
      pilha, itens, idade.sem_data ? vazio(plural(idade.sem_data, "chamado sem data de abertura", "chamados sem data de abertura") + ".") : null);
  }
  function blocoSistemas(estoque) {
    var linhas = Object.keys(obj(estoque.por_sistema)).map(function (s) {
      var b = estoque.por_sistema[s], velhos = b.acima_de_90_dias || 0;
      return { rotulo: s, valor: b.abertos || 0,
        partes: [{ valor: (b.abertos || 0) - velhos, cor: "--seq-2", nome: "até 90 dias" }, { valor: velhos, cor: "--seq-4", nome: "mais de 90 dias" }],
        nota: "mais antigo: " + int(b.mais_antigo_dias) + " d" };
    });
    return cartao("Em aberto por sistema", "O sistema sai da categoria do chamado. A parte escura está aberta há mais de 90 dias.",
      "meio", legenda([["até 90 dias", "--seq-2"], ["mais de 90 dias", "--seq-4"]]),
      linhas.length ? barras(linhas) : vazio("Nenhum chamado em aberto."));
  }
  function blocoStatus(estoque) {
    var dev = lista(obj(estoque.desenvolvimento).status_considerados_dev);
    var linhas = Object.keys(obj(estoque.por_status)).map(function (s) {
      var n = estoque.por_status[s];
      return { rotulo: s, valor: n, selo: dev.indexOf(s) >= 0 ? "dev" : null, partes: [{ valor: n, cor: "--marca", nome: s }] };
    });
    return cartao("Em aberto por status", "Em aberto é todo status menos Fechado. O selo “dev” marca os status que contam como " +
      "“com o desenvolvimento”.", "meio", linhas.length ? barras(linhas) : vazio("Nenhum chamado em aberto."));
  }
  function blocoMatriz(estoque) {
    var matriz = obj(estoque.tecnico_por_status), status = Object.keys(obj(estoque.por_status));
    var nomes = Object.keys(matriz);
    if (!nomes.length) return null;
    var maior = 1;
    nomes.forEach(function (n) { status.forEach(function (s) { maior = Math.max(maior, matriz[n][s] || 0); }); });
    var colunas = [{ titulo: "Responsável", valor: function (n) { return n; } }]
      .concat(status.map(function (s) {
        return { titulo: s, classe: "num girado", valor: function (n) {
          var v = matriz[n][s] || 0, peso = Math.round(8 + 82 * v / maior);
          var span = el("span", { texto: v ? int(v) : "·" });
          span.setAttribute("data-peso", v ? peso : 0);
          return span;
        } };
      }))
      .concat([{ titulo: "Total", classe: "num forte", valor: function (n) {
        return int(status.reduce(function (soma, s) { return soma + (matriz[n][s] || 0); }, 0)); } }]);
    var t = tabela(colunas, nomes, "matriz");
    // a intensidade da célula vai no <td>, não no texto
    Array.prototype.forEach.call(t.querySelectorAll("td.num span[data-peso]"), function (span) {
      var td = span.parentNode, peso = +span.getAttribute("data-peso");
      td.textContent = span.textContent;
      if (!peso) { td.classList.add("zero"); return; }
      td.classList.add("celula");
      td.style.setProperty("--peso", peso);
      if (peso > 55) td.setAttribute("data-escuro", "");
    });
    return cartao("Quem está com o quê", "Cada chamado em aberto aparece uma vez: na linha de quem é o responsável e na coluna do " +
      "status em que está. Quanto mais escura a célula, mais chamados.", "", t);
  }
  function blocoAntigos(estoque) {
    var antigos = lista(estoque.mais_antigos);
    if (!antigos.length) return null;
    return cartao("Os mais antigos em aberto", "Os " + antigos.length + " chamados abertos há mais tempo. A contagem completa está nos quadros acima.", "",
      tabela([
        { titulo: "Chamado", classe: "num", valor: function (c) { return "#" + c.id; } },
        { titulo: "Dias", classe: "num forte", valor: function (c) { return int(c.dias_aberto); } },
        { titulo: "Assunto", valor: function (c) { return c.assunto; } },
        { titulo: "Cliente", valor: function (c) { return c.local; } },
        { titulo: "Sistema", valor: function (c) { return c.sistema; } },
        { titulo: "Status", valor: function (c) { return c.status; } },
        { titulo: "Responsável", valor: function (c) { return c.tecnico; } }
      ], antigos));
  }
  function faixaAgora(chamados) {
    var e = obj(chamados.estoque), fr = obj(chamados.frescor), total = e.total_abertos;
    var idade = obj(e.idade), dev = obj(e.desenvolvimento);
    var maisAntigo = Math.max.apply(null, Object.keys(obj(e.por_sistema)).map(function (s) { return e.por_sistema[s].mais_antigo_dias || 0; }).concat([0]));
    var f = faixa("agora", "Agora", "Foto da fila. A fila inteira foi conferida em " + diaHora(fr.ultima_completa) +
      "; a coleta rápida só atualiza os chamados recentes.");
    f.appendChild(el("div", { "class": "kpis" },
      kpi("Chamados em aberto", int(total), "todos os status menos Fechado"),
      kpi("Abertos há mais de 90 dias", int(idade.mais_de_90_dias), pct(idade.mais_de_90_dias, total) + " da fila"),
      kpi("Mais antigo", int(maisAntigo), "desde a abertura", "dias"),
      kpi("Com o desenvolvimento", int(dev.em_status_dev), pct(dev.em_status_dev, total) + " da fila, pelo status")));
    if (e.aguardando_confirmacao > 0) f.appendChild(el("p", { "class": "aviso", style: "margin:0 0 14px", texto:
      plural(e.aguardando_confirmacao, "chamado não veio", "chamados não vieram") + " na última conferência da fila e " +
      (e.aguardando_confirmacao === 1 ? "aguarda" : "aguardam") + " confirmação de fechamento. Até a próxima conferência, " +
      (e.aguardando_confirmacao === 1 ? "segue contado como aberto" : "seguem contados como abertos") +
      ": sem " + (e.aguardando_confirmacao === 1 ? "ele" : "eles") + ", a fila tem " + int(total - e.aguardando_confirmacao) + "." }));
    var grade = el("div", { "class": "grade" }, blocoIdade(e), blocoSistemas(e), blocoStatus(e), blocoMatriz(e), blocoAntigos(e));
    f.appendChild(grade);
    return f;
  }

  /* ---- DESENVOLVIMENTO --------------------------------------------------- */
  function faixaDev(chamados) {
    var e = obj(chamados.estoque), dev = obj(e.desenvolvimento), total = e.total_abertos;
    var f = faixa("desenvolvimento", "Desenvolvimento", "Duas contas diferentes, que se sobrepõem: pelo status do chamado e por quem é o responsável.");
    f.appendChild(el("div", { "class": "kpis" },
      kpi("Em status de desenvolvimento", int(dev.em_status_dev), pct(dev.em_status_dev, total) + " da fila · " +
        lista(dev.status_considerados_dev).join(", ")),
      kpi("No nome de desenvolvedores", int(dev.atribuidos_a_devs), pct(dev.atribuidos_a_devs, total) + " da fila, em qualquer status")));
    var grade = el("div", { "class": "grade" });
    var porSistema = Object.keys(obj(dev.em_status_dev_por_sistema)).map(function (s) {
      var n = dev.em_status_dev_por_sistema[s];
      return { rotulo: s, valor: n, partes: [{ valor: n, cor: "--marca", nome: s }] };
    });
    var porDev = obj(dev.por_dev), nomes = Object.keys(porDev).sort(function (a, b) { return (porDev[b].abertos || 0) - (porDev[a].abertos || 0); });
    if (nomes.length) {
      var teto = Math.max.apply(null, nomes.map(function (n) { return porDev[n].abertos || 0; }).concat([1]));
      grade.appendChild(cartao("Carga por desenvolvedor", "Chamados em aberto no nome de cada dev. “Em trabalho” são os que estão em " +
        lista(dev.status_considerados_trabalho).join(" ou ") + ". Quem é dev é configuração, não vem do Milldesk.", "dois-tercos",
        tabela([
          { titulo: "Desenvolvedor", valor: function (n) { return n; } },
          { titulo: "Equipe", classe: "apagado", valor: function (n) { return porDev[n].equipe; } },
          { titulo: "Em aberto", valor: function (n) {
            var b = porDev[n], velhos = b.acima_de_90_dias || 0;
            return barras([{ rotulo: "", valor: b.abertos || 0, partes: [
              { valor: (b.abertos || 0) - velhos, cor: "--seq-2", nome: "até 90 dias" }, { valor: velhos, cor: "--seq-4", nome: "mais de 90 dias" }] }], teto);
          } },
          { titulo: "Em trabalho", classe: "num", valor: function (n) { return int(porDev[n].em_trabalho); } },
          { titulo: "+90 dias", classe: "num", valor: function (n) { return int(porDev[n].acima_de_90_dias); } },
          { titulo: "Mais antigo", classe: "num", valor: function (n) { return num(porDev[n].mais_antigo_dias) === null ? "—" : int(porDev[n].mais_antigo_dias) + " d"; } }
        ], nomes, "tabela-dev")));
    }
    grade.appendChild(cartao("Em status de desenvolvimento, por sistema", "Onde está o que espera o desenvolvimento.",
      nomes.length ? "terco" : "", porSistema.length ? barras(porSistema) : vazio("Nenhum chamado em status de desenvolvimento.")));
    f.appendChild(grade);
    return f;
  }

  /* ---- PERÍODO (fluxo) --------------------------------------------------- */
  function grafico(canvas, config) {
    if (typeof Chart === "undefined") return;
    estado.graficos.push(new Chart(canvas.getContext("2d"), config));
  }
  function opcoesGrafico(comLegenda) {
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: comLegenda, position: "top", align: "start", labels: { boxWidth: 12, boxHeight: 12, color: cor("--tinta-2") } },
        tooltip: { callbacks: { title: function (itens) { return itens.length ? "Dia " + itens[0].label : ""; } } } },
      scales: { x: { grid: { display: false }, ticks: { color: cor("--tinta-2"), maxRotation: 0, autoSkipPadding: 12 } },
        y: { beginAtZero: true, grid: { color: cor("--grade") }, border: { display: false }, ticks: { color: cor("--tinta-2"), precision: 0 } } }
    };
  }
  function trocarPeriodo(dias) {
    estado.dias = dias;
    guardarPreferencia(dias);
    desenhar();
  }
  function seletor() {
    var s = el("div", { "class": "seletor", role: "group", "aria-label": "Período" });
    PERIODOS.forEach(function (d) {
      var b = el("button", { type: "button", "aria-pressed": String(d === estado.dias), texto: d + " dias" });
      b.addEventListener("click", function () { trocarPeriodo(d); });
      s.appendChild(b);
    });
    return s;
  }
  function faixaPeriodo(chamados) {
    var fl = obj(chamados.fluxo), fr = obj(chamados.frescor), resol = obj(chamados.resolucao);
    var dias = Object.keys(obj(fl.por_dia)).sort().slice(-estado.dias);
    var soma = function (campo) { return dias.reduce(function (t, d) { return t + (fl.por_dia[d][campo] || 0); }, 0); };
    var criados = soma("criados_clientes"), fechados = soma("fechados"), saldo = criados - fechados;
    var f = faixa("periodo", "Período", "O que aconteceu em cada dia, contado pelo dia do fato (abertura ou fechamento), de " +
      diaMes(dias[0]) + " a " + diaMes(dias[dias.length - 1]) + ".", seletor());
    f.appendChild(el("div", { "class": "kpis" },
      kpi("Criados por clientes", int(criados), "sem internos e sem atendimento diário"),
      kpi("Fechados", int(fechados), "sem atendimento diário"),
      kpi("Saldo da fila", (saldo > 0 ? "+" : "") + int(saldo), saldo > 0 ? "entrou mais do que saiu" : saldo < 0 ? "saiu mais do que entrou" : "entrada igual à saída"),
      kpi("Atendimentos diários", int(soma("atendimentos")), "registros feitos pelos técnicos")));

    var rotulos = dias.map(diaMes);
    var c1 = el("canvas", { role: "img", "aria-label": "Chamados criados por clientes e chamados fechados, por dia" });
    var c2 = el("canvas", { role: "img", "aria-label": "Atendimentos diários por dia" });
    var grade = el("div", { "class": "grade" },
      cartao("Entrada e saída por dia", "Criados por clientes contra fechados, dia a dia. Fechamento só é conhecido para o que o banco " +
        "acompanhou: o histórico de mudanças vale desde " + diaHora(fr.historico_desde) + ".", "dois-tercos", el("div", { "class": "grafico" }, c1)),
      cartao("Atendimentos diários", "Registros que o técnico abre e fecha na hora. Não entram em criados nem em fechados.", "terco",
        el("div", { "class": "grafico" }, c2)));
    f.appendChild(grade);
    grafico(c1, { type: "bar", data: { labels: rotulos, datasets: [
      { label: "Criados por clientes", data: dias.map(function (d) { return fl.por_dia[d].criados_clientes || 0; }), backgroundColor: cor("--serie-1"), borderRadius: 3 },
      { label: "Fechados", data: dias.map(function (d) { return fl.por_dia[d].fechados || 0; }), backgroundColor: cor("--serie-2"), borderRadius: 3 }] },
      options: opcoesGrafico(true) });
    grafico(c2, { type: "bar", data: { labels: rotulos, datasets: [
      { label: "Atendimentos", data: dias.map(function (d) { return fl.por_dia[d].atendimentos || 0; }), backgroundColor: cor("--serie-neutra"), borderRadius: 3 }] },
      options: opcoesGrafico(false) });

    // resolução e clientes vêm prontos para a janela inteira do arquivo: o seletor não os altera
    var janela = resol.dias || fl.dias || 30;
    var grade2 = el("div", { "class": "grade", style: "margin-top:14px" });
    var sistemas = Object.keys(obj(resol.por_sistema));
    grade2.appendChild(cartao("Tempo de resolução · últimos " + janela + " dias", "Tempo entre abrir e fechar, dos chamados fechados na janela. " +
      "Mediana: metade fechou em até esse tempo. Sem atendimento diário. Não muda com o seletor acima.", "meio",
      el("div", { "class": "pilha-legenda" },
        el("div", { "class": "pilha-item", estilo: { "--cor": "var(--marca)" } }, el("b", { texto: duracao(resol.mediana_horas) }), el("span", { texto: "mediana de " + int(resol.fechados) + " fechados" })),
        el("div", { "class": "pilha-item", estilo: { "--cor": "var(--seq-2)" } }, el("b", { texto: pct(resol.no_mesmo_dia, resol.fechados) }), el("span", { texto: "em até 24 h (" + int(resol.no_mesmo_dia) + ")" })),
        el("div", { "class": "pilha-item", estilo: { "--cor": "var(--seq-4)" } }, el("b", { texto: pct(resol.mais_de_7_dias, resol.fechados) }), el("span", { texto: "mais de 7 dias (" + int(resol.mais_de_7_dias) + ")" }))),
      sistemas.length ? tabela([
        { titulo: "Sistema", valor: function (s) { return s; } },
        { titulo: "Fechados", classe: "num", valor: function (s) { return int(resol.por_sistema[s].fechados); } },
        { titulo: "Mediana", classe: "num forte", valor: function (s) { return duracao(resol.por_sistema[s].mediana_horas); } },
        { titulo: "Até 24 h", classe: "num", valor: function (s) { return pct(resol.por_sistema[s].no_mesmo_dia, resol.por_sistema[s].fechados); } },
        { titulo: "+7 dias", classe: "num", valor: function (s) { return pct(resol.por_sistema[s].mais_de_7_dias, resol.por_sistema[s].fechados); } }
      ], sistemas) : vazio("Nenhum chamado fechado na janela."),
      resol.fechados_sem_data ? vazio(plural(resol.fechados_sem_data, "chamado fechado sem data de fechamento ficou", "chamados fechados sem data de fechamento ficaram") + " fora da conta.") : null));

    var clientes = Object.keys(obj(fl.criados_por_cliente));
    var topo = clientes.slice(0, MAX_CLIENTES).map(function (c) {
      var n = fl.criados_por_cliente[c].total || 0;
      return { rotulo: c, valor: n, partes: [{ valor: n, cor: "--serie-1", nome: c }] };
    });
    grade2.appendChild(cartao("Clientes que mais abriram chamado · últimos " + (fl.dias || 30) + " dias",
      "Cliente é o campo “local” do Milldesk. Sem a própria Sino e sem atendimento diário. Não muda com o seletor acima.", "meio",
      topo.length ? barras(topo) : vazio("Nenhum chamado de cliente na janela."),
      clientes.length > MAX_CLIENTES ? vazio("Mostrando " + MAX_CLIENTES + " de " + clientes.length + " clientes.") : null));
    f.appendChild(grade2);
    return f;
  }

  /* ---- MUDANÇAS (eventos) ------------------------------------------------ */
  var TIPOS = { entrou: "novo", fechou: "fechou", status: "status", dono: "responsável", tecnico: "responsável", reabriu: "reabriu" };
  function faixaMudancas(chamados) {
    var eventos = lista(chamados.eventos);
    var f = faixa("mudancas", "Últimas mudanças", "O que mudou entre uma coleta e a seguinte. A hora é a da coleta que percebeu a mudança, " +
      "não a do fato; duas mudanças no mesmo intervalo aparecem como uma.");
    var ul = el("ul", { "class": "eventos" });
    eventos.forEach(function (e) {
      var trecho = e.tipo === "entrou" ? "entrou em “" + (e.para || "—") + "”"
        : (e.de || "—") + " → " + (e.para || "—");
      ul.appendChild(el("li", {},
        el("span", { "class": "apagado", texto: diaMes(e.momento) + " " + hora(e.momento) }),
        el("span", { "class": "evento-tipo", "data-tipo": e.tipo, texto: TIPOS[e.tipo] || e.tipo }),
        el("span", { "class": "evento-texto" }, el("b", { texto: "#" + e.chamado_id }), " ", e.assunto || "(sem assunto)",
          el("div", { "class": "apagado", texto: [trecho, e.sistema, e.tecnico].filter(Boolean).join(" · ") }))));
    });
    f.appendChild(cartao("Mudanças observadas", "As " + eventos.length + " mais recentes.", "",
      eventos.length ? ul : vazio("Nenhuma mudança registrada ainda.")));
    return f;
  }

  /* ---- LICENÇAS ---------------------------------------------------------- */
  function seloDias(d) {
    if (num(d) === null) return "—";
    var texto = d === 0 ? "hoje" : d > 0 ? "em " + plural(d, "dia", "dias") : "há " + plural(-d, "dia", "dias");
    return el("span", { "class": "dias", "data-estado": d <= 0 ? "alerta" : null, texto: texto });
  }
  function tabelaLicencas(itens) {
    return tabela([
      { titulo: "Cliente", valor: function (l) { return l.cliente; } },
      { titulo: "Sistema", valor: function (l) { return l.sistema; } },
      { titulo: "Vencimento", classe: "num", valor: function (l) { return l.vencimento; } },
      { titulo: "Prazo", classe: "num", valor: function (l) { return seloDias(l.dias); } }
    ], itens);
  }
  function avisoLicencas(coletado) {
    var min = minutosDesde(coletado);
    if (min !== null && min <= LICENCAS_ALERTA_HORAS * 60) return null;
    return el("p", { "class": "aviso", "data-estado": "alerta", texto: "Os dados de licenças não são atualizados " + haQuanto(min) + "." });
  }
  function faixaLicencas(lic) {
    var janela = lic.janela_dias || 30;
    var f = faixa("licencas", "Licenças", "Coletado em " + diaHora(lic.coletado_em) + ". Período de " + janela + " dias para os dois lados.");
    if (lic.indisponivel) { f.appendChild(cartao("Licenças", null, "", vazio("Fonte indisponível: o sistema de licenças não respondeu."))); return f; }
    var porDias = function (a, b) { return (a.dias || 0) - (b.dias || 0); };
    var vencendo = lista(lic.vencendo_em_breve).slice().sort(porDias);
    var vencidas = lista(lic.vencidas_recentes).slice().sort(porDias).reverse();
    var aviso = avisoLicencas(lic.coletado_em);
    if (aviso) f.appendChild(aviso);
    f.appendChild(el("div", { "class": "kpis", style: aviso ? "margin-top:14px" : null },
      kpi("Vencem nos próximos " + janela + " dias", int(vencendo.length), "ainda válidas"),
      kpi("Venceram nos últimos " + janela + " dias", int(vencidas.length), "vence hoje conta como vencida"),
      kpi("Vencidas há mais tempo", int(lic.vencidas_antigas_total), "fora do período, só a contagem"),
      kpi("Vencem depois do período", int(lic.vencendo_alem_da_janela), "fora do período, só a contagem")));
    f.appendChild(el("div", { "class": "grade" },
      cartao("Vencendo", "Da mais próxima para a mais distante. Fonte: painel do sistema de licenças.", "meio",
        vencendo.length ? tabelaLicencas(vencendo) : vazio("Nenhuma licença vence no período.")),
      cartao("Vencidas", "Da mais recente para a mais antiga.", "meio",
        vencidas.length ? tabelaLicencas(vencidas) : vazio("Nenhuma licença venceu no período."))));
    return f;
  }
  function faixaLicencasResumo(r) {
    var f = faixa("licencas", "Licenças", r.indisponivel ? null : "Coletado em " + diaHora(r.coletado_em) + ". Só as contagens.");
    if (r.indisponivel) { f.appendChild(cartao("Licenças", null, "", vazio("Fonte indisponível: o sistema de licenças não respondeu."))); return f; }
    var janela = r.janela_dias || 30, aviso = avisoLicencas(r.coletado_em);
    if (aviso) f.appendChild(aviso);
    f.appendChild(el("div", { "class": "kpis", style: aviso ? "margin-top:14px" : null },
      kpi("Vencem nos próximos " + janela + " dias", int(r.vencendo_em_breve)),
      kpi("Venceram nos últimos " + janela + " dias", int(r.vencidas_recentes)),
      kpi("Vencidas há mais tempo", int(r.vencidas_antigas_total))));
    return f;
  }

  /* ---- AGENDA ------------------------------------------------------------ */
  function faixaAgenda(ag) {
    var f = faixa("agenda", "Agenda", ag.data ? "Dia " + diaMes(ag.data) + ". Coletada em " + diaHora(ag.coletado_em) + "." : null);
    if (ag.oculta) { f.appendChild(cartao("Agenda", null, "", vazio("Agenda oculta: a exibição de nomes está desligada."))); return f; }
    if (ag.indisponivel) { f.appendChild(cartao("Agenda", null, "", vazio("Fonte indisponível: a agenda não respondeu."))); return f; }
    var eventos = lista(ag.eventos), inteiro = lista(ag.dia_inteiro);
    var grade = el("div", { "class": "grade" });
    grade.appendChild(cartao("Compromissos do dia", "Calendários da equipe. Fonte: Google Agenda.", inteiro.length ? "dois-tercos" : "",
      eventos.length ? tabela([
        { titulo: "Horário", classe: "num", valor: function (e) { return [e.inicio, e.fim].filter(Boolean).join(" – "); } },
        { titulo: "Compromisso", classe: "forte", valor: function (e) { return e.titulo; } },
        { titulo: "Onde", valor: function (e) { return e.reuniao_online ? "online" : e.local; } },
        { titulo: "Calendário", classe: "apagado", valor: function (e) { return lista(e.calendarios).join(", "); } }
      ], eventos) : vazio("Nenhum compromisso com horário hoje.")));
    if (inteiro.length) grade.appendChild(cartao("O dia inteiro", null, "terco", tabela([
      { titulo: "Evento", valor: function (e) { return e.titulo; } },
      { titulo: "Calendário", classe: "apagado", valor: function (e) { return lista(e.calendarios).join(", "); } }
    ], inteiro)));
    f.appendChild(grade);
    return f;
  }

  /* ---- montagem por público ---------------------------------------------- */
  var PAGINAS = {
    suporte: ["agora", "desenvolvimento", "periodo", "mudancas"],
    desenvolvimento: ["desenvolvimento", "agora", "periodo", "mudancas"],
    diretor: ["agora", "desenvolvimento", "periodo", "licencas-resumo", "agenda"],
    licencas: ["licencas"]
  };
  var NOMES = { agora: "Agora", desenvolvimento: "Desenvolvimento", periodo: "Período", mudancas: "Mudanças",
    "licencas-resumo": "Licenças", licencas: "Licenças", agenda: "Agenda" };
  var DE_CHAMADOS = { agora: faixaAgora, desenvolvimento: faixaDev, periodo: faixaPeriodo, mudancas: faixaMudancas };

  function desenhar() {
    var dados = estado.dados, raiz = document.getElementById("conteudo");
    var rolagem = window.scrollY;
    estado.graficos.forEach(function (g) { g.destroy(); });
    estado.graficos = [];
    raiz.textContent = "";
    if (!dados) return;
    var chamados = obj(dados.chamados), semChamados = false;
    PAGINAS[PUBLICO].forEach(function (secao) {
      if (DE_CHAMADOS[secao] && (chamados.indisponivel || !dados.chamados)) {
        if (!semChamados) raiz.appendChild(el("p", { "class": "aviso", "data-estado": "alerta",
          texto: "Chamados indisponíveis: o banco de dados não pôde ser lido na última exportação." }));
        semChamados = true;
        return;
      }
      // cada seção é isolada: dado quebrado numa delas vira aviso, e as outras aparecem
      var antes = estado.graficos.length;
      try {
        if (DE_CHAMADOS[secao]) raiz.appendChild(DE_CHAMADOS[secao](chamados));
        else if (secao === "licencas") raiz.appendChild(faixaLicencas(obj(dados.licencas)));
        else if (secao === "licencas-resumo") raiz.appendChild(faixaLicencasResumo(obj(dados.licencas_resumo)));
        else if (secao === "agenda") raiz.appendChild(faixaAgenda(obj(dados.agenda)));
      } catch (erro) {
        estado.graficos.splice(antes).forEach(function (g) { g.destroy(); });
        var f = faixa(secao === "licencas-resumo" ? "licencas" : secao, NOMES[secao]);
        f.appendChild(el("p", { "class": "aviso", "data-estado": "alerta",
          texto: "Esta seção não pôde ser mostrada: o dado veio em formato inesperado. As outras seções não são afetadas." }));
        raiz.appendChild(f);
        if (window.console) console.error("seção " + secao, erro);
      }
    });
    raiz.appendChild(el("p", { "class": "rodape", texto: "Arquivo gerado em " + diaHora(dados.gerado_em) +
      ". A página busca dados novos a cada " + BUSCA_SEGUNDOS + " segundos." }));
    window.scrollTo(0, rolagem);
  }

  function carimbo() {
    var alvo = document.getElementById("frescor"), dados = estado.dados;
    if (!alvo) return;
    var referencia = null, rotulo = "Dados";
    if (dados) {
      if (PUBLICO === "licencas") { referencia = obj(dados.licencas).coletado_em; rotulo = "Licenças"; }
      else { referencia = obj(obj(dados.chamados).frescor).ultima_coleta; rotulo = "Chamados"; }
    }
    var min = minutosDesde(referencia);
    var limites = PUBLICO === "licencas" ? [LICENCAS_ALERTA_HORAS * 60, LICENCAS_ALERTA_HORAS * 60] : [FRESCOR_ATENCAO_MIN, FRESCOR_ALERTA_MIN];
    var estadoCarimbo = min === null || min > limites[1] ? "alerta" : min > limites[0] ? "atencao" : "ok";
    var texto = min === null ? "sem dados" : "atualizado " + haQuanto(min) + " (" + diaMes(referencia) + " " + hora(referencia) + ")";
    if (estado.falha) { estadoCarimbo = "alerta"; texto = "sem acesso aos dados; " + (min === null ? "nada para mostrar" : "mostrando os de " + haQuanto(min)); }
    else if (dados && obj(obj(dados.chamados).frescor).limite_atingido) { estadoCarimbo = "alerta"; texto += " · a última coleta foi barrada pelo limite do Milldesk"; }
    alvo.setAttribute("data-estado", estadoCarimbo);
    alvo.textContent = "";
    alvo.appendChild(el("b", { texto: rotulo + ": " }));
    alvo.appendChild(document.createTextNode(texto));
  }

  function menu() {
    var nav = document.getElementById("nav");
    PAGINAS[PUBLICO].forEach(function (s) {
      var id = s === "licencas-resumo" ? "licencas" : s;
      nav.appendChild(el("a", { href: "#" + id, texto: NOMES[s] }));
    });
  }

  function buscar() {
    return fetch("dados/" + PUBLICO + ".json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (dados) {
        if (!dados || dados.publico !== PUBLICO) throw new Error("arquivo de outro público");
        estado.falha = null;
        var assinatura = JSON.stringify([dados.gerado_em, obj(obj(dados.chamados).frescor).ultima_coleta]);
        // a assinatura só é guardada depois de desenhar: se o desenho falhar, a próxima busca tenta de novo
        if (assinatura !== estado.assinatura) { estado.dados = dados; desenhar(); estado.assinatura = assinatura; }
      })
      .catch(function (erro) { estado.falha = String(erro && erro.message || erro); })
      .then(carimbo);
  }

  if (!PAGINAS[PUBLICO]) { document.getElementById("conteudo").textContent = "Página sem público definido."; return; }
  menu();
  buscar();
  setInterval(buscar, BUSCA_SEGUNDOS * 1000);
  setInterval(carimbo, 30 * 1000);
})();
