# Guia de Anotação de Ground Truth — a11y-autofix Dataset

## Diretrizes Completas para Anotadores

---

**Versão**: 1.0
**Destinado a**: Anotadores humanos responsáveis pela validação de findings de acessibilidade
**Pré-requisito**: Leitura completa deste guia antes de iniciar qualquer sessão de anotação
**Referência normativa**: WCAG 2.1 AA (W3C, 2018) e WCAG 2.2 (W3C, 2023)

---

## Sumário

1. [Visão Geral do Processo de Anotação](#1-visão-geral-do-processo-de-anotação)
2. [Os Três Rótulos de Anotação](#2-os-três-rótulos-de-anotação)
3. [Fluxo de Decisão Passo a Passo](#3-fluxo-de-decisão-passo-a-passo)
4. [Interface de Anotação](#4-interface-de-anotação)
5. [Guia por Tipo de Issue](#5-guia-por-tipo-de-issue)
   - 5.1 [CONTRAST — Contraste de Cor](#51-contrast--contraste-de-cor)
   - 5.2 [ALT_TEXT — Texto Alternativo](#52-alt_text--texto-alternativo)
   - 5.3 [LABEL — Rótulos de Formulário](#53-label--rótulos-de-formulário)
   - 5.4 [ARIA — Atributos ARIA](#54-aria--atributos-aria)
   - 5.5 [KEYBOARD — Navegação por Teclado](#55-keyboard--navegação-por-teclado)
   - 5.6 [FOCUS — Indicador de Foco](#56-focus--indicador-de-foco)
   - 5.7 [SEMANTIC — Semântica HTML](#57-semantic--semântica-html)
6. [Casos Limítrofes e Ambíguos](#6-casos-limítrofes-e-ambíguos)
7. [Erros Comuns de Anotadores](#7-erros-comuns-de-anotadores)
8. [Protocolo de Reconciliação de Discordâncias](#8-protocolo-de-reconciliação-de-discordâncias)
9. [Calibração e Exercícios de Treinamento](#9-calibração-e-exercícios-de-treinamento)
10. [Referências Rápidas WCAG](#10-referências-rápidas-wcag)

---

## 1. Visão Geral do Processo de Anotação

### O que você está anotando?

Você está avaliando **findings de acessibilidade** detectados automaticamente por ferramentas (pa11y, axe-core, Playwright+axe) em componentes React/TypeScript de projetos reais de código aberto. Cada finding foi detectado por **apenas uma ferramenta** (tool_consensus = 1). Seu papel é determinar se o finding representa uma **violação genuína das WCAG 2.1/2.2** ou se é um **falso positivo** da ferramenta.

> **Por que apenas consensus = 1?** Findings detectados por duas ou mais ferramentas independentes (consensus ≥ 2) já são aceitos automaticamente como ground truth — a probabilidade de que duas ferramentas concordem erroneamente é muito baixa (~2%). Você revisa apenas os casos ambíguos.

### O que você NÃO precisa fazer

- ❌ Avaliar se o código está "bem escrito" ou segue boas práticas
- ❌ Verificar se o componente funciona corretamente além da acessibilidade
- ❌ Propor correções (isso é papel do sistema de reparo)
- ❌ Pesquisar o projeto no GitHub para ver outras partes do código
- ❌ Testar o componente em um navegador real (use apenas o código fornecido)

### O que você DEVE fazer

- ✅ Avaliar o **trecho de código** exibido na interface em relação ao **critério WCAG** informado
- ✅ Considerar apenas o contexto visível (você não tem acesso ao projeto completo)
- ✅ Aplicar as diretrizes deste guia de forma consistente e documentar dúvidas
- ✅ Registrar **notas textuais** sempre que sua decisão não for imediata

---

## 2. Os Três Rótulos de Anotação

### `CONFIRMED` — Violação Confirmada

Use quando você verifica que **o finding descreve uma violação genuína e não-trivial das WCAG** no código apresentado, considerando o contexto disponível.

**Critérios para CONFIRMED:**
- O problema descrito pela ferramenta é verificável no código exibido
- Nenhuma solução alternativa (técnica de acessibilidade equivalente) está presente no trecho visível
- Um usuário de tecnologia assistiva seria adversamente afetado

### `FALSE_POSITIVE` — Falso Positivo

Use quando você verifica que **a ferramenta alertou incorretamente** — o elemento em questão é acessível apesar do alerta, ou o alerta não é aplicável ao contexto.

**Critérios para FALSE_POSITIVE:**
- O código apresenta uma solução alternativa válida que a ferramenta não reconheceu
- O critério WCAG não é aplicável ao tipo de elemento em questão
- A ferramenta aplicou uma heurística incorreta ao contexto específico

### `UNCERTAIN` — Incerto

Use quando **não é possível determinar com confiança** se o finding é genuíno ou falso positivo com o contexto disponível.

**Critérios para UNCERTAIN:**
- A decisão correta depende de informações não presentes no trecho de código (ex.: como o componente é usado, qual conteúdo dinâmico renderiza)
- Você conhece o princípio WCAG mas tem dúvida genuína sobre sua aplicação neste caso específico
- O finding cai em uma zona cinzenta documentada neste guia

> ⚠️ **Use UNCERTAIN com parcimônia.** Findings UNCERTAIN são excluídos do corpus de avaliação primário. Se você consegue fazer um julgamento razoável, prefira CONFIRMED ou FALSE_POSITIVE e adicione uma nota explicando sua incerteza.

---

## 3. Fluxo de Decisão Passo a Passo

Para cada finding apresentado, siga este fluxo:

```
┌─────────────────────────────────────────────────────────────┐
│  PASSO 1: Identificar o critério WCAG e o tipo de issue     │
│  Consulte a seção 5 para o tipo de issue correspondente     │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  PASSO 2: Localizar o elemento problemático no código       │
│  Use o seletor CSS e a mensagem da ferramenta para          │
│  identificar qual elemento está sendo questionado           │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  PASSO 3: Verificar se há solução alternativa no código     │
│  Existe aria-label? role? title? texto visível adjacente?   │
│  O elemento é decorativo/oculto propositalmente?            │
└──────────┬──────────────────────────────────┬───────────────┘
           │ SIM (solução presente)            │ NÃO (sem solução)
           ▼                                  ▼
┌──────────────────────┐            ┌──────────────────────────┐
│ A solução é válida   │            │ O critério WCAG aplica-  │
│ segundo WCAG?        │            │ se a este elemento?      │
└──────┬───────┬───────┘            └──────┬─────────┬─────────┘
       │ SIM   │ NÃO                       │ SIM     │ NÃO
       ▼       ▼                           ▼         ▼
  FALSE_    CONFIRMED              CONFIRMED    FALSE_POSITIVE
  POSITIVE


  Se ainda houver dúvida genuína após estes passos → UNCERTAIN
```

---

## 4. Interface de Anotação

A interface exibida pelo script `annotate.py` apresenta as seguintes informações para cada finding:

```
────────────────────────────────────────────────────────────
  Finding ID : a3f7c91b2d4e5f60
  File       : src/components/checkout/PaymentForm.tsx
  Selector   : .payment-form input[type="email"]
  WCAG       : 1.3.1
  Issue type : label
  Confidence : medium  |  Consensus: 1
  Impact     : serious
  Tools      : axe-core
  Message    : Form element does not have an accessible label
────────────────────────────────────────────────────────────
  [c] confirmed  |  [f] false_positive  |  [u] uncertain  |  [s] skip
  [annotator_1] label >
```

### Como interpretar cada campo

| Campo | O que significa |
|-------|----------------|
| **Finding ID** | Identificador único (hash SHA-256 truncado). Use-o em notas de reconciliação. |
| **File** | Caminho do arquivo onde o elemento problemático foi encontrado |
| **Selector** | Seletor CSS que identifica o elemento no DOM. Use-o para localizar o elemento no código. |
| **WCAG** | Critério de sucesso WCAG que a ferramenta alega estar violado (ex: 1.3.1, 4.1.2) |
| **Issue type** | Categoria do problema (ver Seção 5) |
| **Confidence** | `medium` = detectado por 1 ferramenta com impacto `serious`/`critical`; `low` = 1 ferramenta, impacto `moderate`/`minor` |
| **Consensus** | Sempre será `1` para findings que chegam à anotação humana |
| **Impact** | `critical` → bloqueia uso; `serious` → dificulta muito; `moderate` → dificulta; `minor` → inconveniente |
| **Tools** | Qual ferramenta detectou (axe-core, pa11y, ou playwright+axe) |
| **Message** | Mensagem diagnóstica original da ferramenta (em inglês) |

### Opção `[s] skip`

Use apenas quando você precisa interromper a sessão temporariamente. Findings ignorados voltarão na próxima sessão. **Não use skip como forma de evitar decisões difíceis** — nesses casos, use `UNCERTAIN` com uma nota.

---

## 5. Guia por Tipo de Issue

---

### 5.1 CONTRAST — Contraste de Cor

**Critérios WCAG**: 1.4.3 (AA), 1.4.6 (AAA), 1.4.11 (Componentes não-texto)

**O que é**: A razão de contraste entre cor de texto e cor de fundo não atinge o mínimo exigido.

**Requisitos WCAG 2.1 AA**:
- Texto normal (< 18pt ou < 14pt negrito): razão mínima **4,5:1**
- Texto grande (≥ 18pt ou ≥ 14pt negrito): razão mínima **3:1**
- Componentes de UI e estados de foco (critério 1.4.11): razão mínima **3:1**

---

#### ✅ Exemplo 1 — CONFIRMED (violação clara)

```tsx
// src/components/ui/Badge.tsx
// Selector: .badge-secondary
// Message: "Element has insufficient color contrast of 2.85:1
//           (foreground: #999999, background: #ffffff, normal text)"

const Badge = ({ label }: { label: string }) => (
  <span
    className="badge-secondary"
    style={{ color: "#999999", backgroundColor: "#ffffff", fontSize: "12px" }}
  >
    {label}
  </span>
);
```

**Decisão: `CONFIRMED`**

**Por quê**: Texto de cor `#999999` sobre fundo `#ffffff` tem razão de contraste ≈ 2,85:1. Texto de 12px (< 18pt) exige 4,5:1 segundo WCAG 1.4.3. A violação é calculável diretamente do código. Nenhuma solução alternativa está presente.

---

#### ✅ Exemplo 2 — CONFIRMED (placeholder de input)

```tsx
// src/components/SearchBar.tsx
// Selector: input[type="search"]::placeholder
// Message: "Placeholder text has insufficient contrast of 2.1:1"
// WCAG: 1.4.3

<input
  type="search"
  placeholder="Search..."
  style={{ color: "#333", backgroundColor: "#fff" }}
/>
```

> **Atenção**: Ferramenta alertou sobre o `::placeholder`. O texto placeholder tem contraste menor que o texto principal.

**Decisão: `CONFIRMED`**

**Por quê**: O WCAG 1.4.3 aplica-se ao texto de placeholder quando ele é o único indicador do propósito do campo. Razão de contraste < 3:1 é insuficiente mesmo para texto descritivo.

---

#### ❌ Exemplo 3 — FALSE_POSITIVE (componente decorativo)

```tsx
// src/components/LoadingSpinner.tsx
// Selector: .spinner-overlay
// Message: "Element has insufficient color contrast of 1.5:1
//           (foreground: #eeeeee, background: #ffffff)"
// WCAG: 1.4.3

const LoadingSpinner = () => (
  <div
    className="spinner-overlay"
    aria-hidden="true"    // ← elemento marcado como decorativo
    role="presentation"
  >
    <div className="spinner-circle" style={{ color: "#eeeeee" }} />
  </div>
);
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O elemento possui `aria-hidden="true"` e `role="presentation"`, sinalizando explicitamente que é decorativo. Critério 1.4.3 não se aplica a conteúdo puramente decorativo (ver WCAG 2.1 §1.1.1 — exceção para decorativo). A ferramenta não reconheceu a marcação ARIA.

---

#### ❌ Exemplo 4 — FALSE_POSITIVE (texto grande)

```tsx
// src/components/Hero.tsx
// Selector: h1.hero-title
// Message: "Element has insufficient color contrast of 3.2:1
//           (foreground: #767676, background: #ffffff)"
// WCAG: 1.4.3

<h1
  className="hero-title"
  style={{ color: "#767676", fontSize: "36px", fontWeight: "bold" }}
>
  Welcome to Our Platform
</h1>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: 36px bold = 27pt negrito, enquadra-se como **texto grande** (≥ 14pt negrito). Para texto grande, o requisito WCAG 1.4.3 AA é **3:1**, não 4,5:1. Uma razão de 3,2:1 **passa** no critério aplicável. A ferramenta aplicou o limiar de texto normal incorretamente.

---

#### ❓ Exemplo 5 — UNCERTAIN (cor definida via variável CSS/token)

```tsx
// src/components/Button.tsx
// Selector: button.btn-primary
// Message: "Element has insufficient color contrast"
// WCAG: 1.4.3

<button
  className="btn-primary"
  style={{ color: "var(--color-primary-text)", backgroundColor: "var(--color-primary-bg)" }}
>
  Submit
</button>
```

**Decisão: `UNCERTAIN`**

**Por quê**: As cores são definidas por variáveis CSS (`--color-primary-text`, `--color-primary-bg`) cujos valores reais não estão visíveis neste arquivo. A razão de contraste efetiva depende dos valores dessas variáveis, que podem ou não cumprir o critério. Sem acesso ao CSS global ou ao design token, não é possível verificar.

**Nota sugerida**: *"Cores definidas por CSS custom properties não visíveis neste arquivo. Verificar valores de --color-primary-text e --color-primary-bg no arquivo de tokens/CSS global."*

---

### 5.2 ALT_TEXT — Texto Alternativo

**Critério WCAG**: 1.1.1 (A)

**O que é**: Imagens informativas sem texto alternativo adequado são inacessíveis para usuários de leitores de tela.

**Regra fundamental**:
- Imagem **informativa** → `alt` deve descrever a informação transmitida
- Imagem **decorativa** → `alt=""` (string vazia), **nunca** omitir o atributo
- Ícone com texto visível adjacente → `alt=""` (o texto adjacente já descreve)
- Ícone sem texto adjacente → `alt` deve descrever a ação/conteúdo

---

#### ✅ Exemplo 6 — CONFIRMED (atributo alt ausente)

```tsx
// src/components/ProductCard.tsx
// Selector: .product-image img
// Message: "img element is missing an alt attribute"
// WCAG: 1.1.1

const ProductCard = ({ product }: Props) => (
  <div className="product-card">
    <img
      src={product.imageUrl}
      className="product-image"
      // ← sem atributo alt
    />
    <h3>{product.name}</h3>
  </div>
);
```

**Decisão: `CONFIRMED`**

**Por quê**: Imagem de produto é claramente informativa (representa visualmente o produto que o usuário está considerando comprar). A ausência total do atributo `alt` é uma violação direta do WCAG 1.1.1 nível A.

---

#### ✅ Exemplo 7 — CONFIRMED (alt genérico/inútil)

```tsx
// src/components/TeamMember.tsx
// Selector: .team-photo img
// Message: "img element has an alt attribute but its value is not descriptive"
// WCAG: 1.1.1

<img
  src={member.photo}
  alt="photo"     // ← alt genérico, não descreve o conteúdo
  className="team-photo"
/>
```

**Decisão: `CONFIRMED`**

**Por quê**: `alt="photo"` não transmite nenhuma informação sobre quem está na foto. Uma foto de membro da equipe é informativa — o leitor de tela leria apenas "photo", sem saber de quem se trata. O alt deveria ser o nome da pessoa (ex: `alt="Maria Silva, Designer"`).

---

#### ❌ Exemplo 8 — FALSE_POSITIVE (ícone decorativo com texto adjacente)

```tsx
// src/components/Sidebar/NavItem.tsx
// Selector: nav a svg
// Message: "SVG element used as an image without accessible text"
// WCAG: 1.1.1

const NavItem = ({ icon: Icon, label }: Props) => (
  <a href="/dashboard" className="nav-item">
    <Icon aria-hidden="true" />  {/* ← decorativo, texto visível ao lado */}
    <span>{label}</span>         {/* ← "Dashboard" — texto visível */}
  </a>
);
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O ícone SVG possui `aria-hidden="true"` e há texto visível (`<span>{label}</span>`) ao lado. O leitor de tela lerá o texto do link corretamente. O padrão de ícone + texto com `aria-hidden` no ícone é uma solução válida e recomendada pelas WCAG.

---

#### ❌ Exemplo 9 — FALSE_POSITIVE (imagem puramente decorativa com alt vazio correto)

```tsx
// src/components/Hero.tsx
// Selector: .hero-background-image img
// Message: "img element alt attribute is empty"
// WCAG: 1.1.1

<img
  src="/images/background-abstract.jpg"
  alt=""    // ← string vazia é correto para decorativo
  className="hero-background-image"
  role="presentation"
/>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: `alt=""` (string vazia) é a **forma correta** de marcar imagens decorativas segundo WCAG 1.1.1 (técnica H67). A combinação de `alt=""` com `role="presentation"` reforça ainda mais que a imagem é decorativa. A ferramenta alertou erroneamente — um alt vazio **em imagem decorativa** é o comportamento correto.

---

#### ✅ Exemplo 10 — CONFIRMED (ícone de ação sem texto)

```tsx
// src/components/DataTable.tsx
// Selector: button.delete-row-btn svg
// Message: "Interactive element does not have an accessible name"
// WCAG: 1.1.1 / 4.1.2

const DeleteButton = ({ onDelete }: Props) => (
  <button onClick={onDelete} className="delete-row-btn">
    <TrashIcon />  {/* ← sem aria-label, sem texto visível */}
  </button>
);
```

**Decisão: `CONFIRMED`**

**Por quê**: Um botão cujo único conteúdo é um ícone SVG sem `aria-label`, `aria-labelledby`, `title` ou texto visível é completamente inacessível para usuários de leitor de tela. O leitor de tela anunciaria "botão" sem nenhuma indicação do que ele faz.

---

#### ❓ Exemplo 11 — UNCERTAIN (alt gerado dinamicamente)

```tsx
// src/components/Gallery.tsx
// Selector: .gallery-item img
// Message: "img element has empty alt attribute on informational image"
// WCAG: 1.1.1

const GalleryItem = ({ item }: { item: GalleryItem }) => (
  <img
    src={item.imageUrl}
    alt={item.description || ""}   // ← depende do dado em runtime
    className="gallery-item"
  />
);
```

**Decisão: `UNCERTAIN`**

**Por quê**: O atributo alt é `item.description || ""`. Se `item.description` sempre conterá uma descrição significativa em produção, a implementação está correta. Se `item.description` frequentemente for vazio ou `null`, então a imagem ficará sem alt descritivo. Sem saber o schema dos dados, não é possível determinar se há violação.

**Nota sugerida**: *"Alt é condicional: se item.description puder ser vazio/null para imagens informativas, é CONFIRMED. Se description sempre é populado, é FALSE_POSITIVE. Verificar schema ou documentação da API."*

---

### 5.3 LABEL — Rótulos de Formulário

**Critérios WCAG**: 1.3.1 (A), 2.4.6 (AA), 3.3.2 (A)

**O que é**: Campos de formulário sem rótulo acessível impedem que usuários de leitores de tela entendam o propósito do campo.

**Formas válidas de rótulo** (em ordem de preferência):
1. Elemento `<label>` com `htmlFor` apontando para o `id` do campo
2. `aria-labelledby` referenciando um elemento com texto descritivo
3. `aria-label` com texto descritivo no próprio elemento
4. `title` como último recurso (não recomendado, mas válido)
5. `placeholder` sozinho **NÃO é rótulo válido** segundo WCAG

---

#### ✅ Exemplo 12 — CONFIRMED (sem label, apenas placeholder)

```tsx
// src/components/LoginForm.tsx
// Selector: input#email
// Message: "Form element does not have an accessible label"
// WCAG: 1.3.1

<div className="field-group">
  <input
    id="email"
    type="email"
    placeholder="Enter your email"  // ← placeholder NÃO substitui label
    // ← sem label, sem aria-label, sem aria-labelledby
  />
</div>
```

**Decisão: `CONFIRMED`**

**Por quê**: O campo usa apenas `placeholder` como indicação de propósito. Segundo WCAG 1.3.1, o `placeholder` não constitui um label acessível: (1) desaparece ao digitar; (2) muitos leitores de tela não anunciam o placeholder; (3) o contraste do placeholder frequentemente falha 1.4.3. O campo não tem nenhuma associação de label válida.

---

#### ❌ Exemplo 13 — FALSE_POSITIVE (aria-label presente)

```tsx
// src/components/SearchBar.tsx
// Selector: input.search-input
// Message: "Form element does not have an accessible label"
// WCAG: 1.3.1

<div className="search-container">
  <input
    type="search"
    className="search-input"
    aria-label="Search products"    // ← label ARIA válido
    placeholder="Search..."
  />
  <button type="submit">
    <SearchIcon aria-hidden="true" />
  </button>
</div>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O campo possui `aria-label="Search products"`, que é um label ARIA válido e suficiente segundo WCAG 1.3.1 e técnica ARIA14. A ferramenta provavelmente não reconheceu o `aria-label` por alguma limitação de parsing. O elemento está corretamente rotulado.

---

#### ❌ Exemplo 14 — FALSE_POSITIVE (label via aria-labelledby)

```tsx
// src/components/QuantityPicker.tsx
// Selector: input#quantity
// Message: "Form element does not have an accessible label"
// WCAG: 1.3.1

<div className="quantity-picker">
  <span id="qty-label">Quantity</span>   {/* ← texto do label */}
  <button aria-label="Decrease">-</button>
  <input
    id="quantity"
    type="number"
    aria-labelledby="qty-label"   // ← referência válida ao span
    min="1"
    max="99"
    defaultValue={1}
  />
  <button aria-label="Increase">+</button>
</div>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O campo usa `aria-labelledby="qty-label"` referenciando um `<span id="qty-label">` existente no mesmo componente. Esta é uma técnica ARIA válida (ARIA16) que fornece um nome acessível ao campo.

---

#### ✅ Exemplo 15 — CONFIRMED (label mal associado — id não coincide)

```tsx
// src/components/ContactForm.tsx
// Selector: input#phone
// Message: "Label's for attribute references a field that doesn't exist"
// WCAG: 1.3.1

<div>
  <label htmlFor="phone-number">Phone</label>  {/* htmlFor: "phone-number" */}
  <input
    id="phone"                                  {/* id: "phone" ← não coincide */}
    type="tel"
    name="phone"
  />
</div>
```

**Decisão: `CONFIRMED`**

**Por quê**: O `htmlFor` do label (`"phone-number"`) não corresponde ao `id` do input (`"phone"`). A associação está quebrada — o leitor de tela não conectará o label ao campo. Para um usuário de teclado, ao focar no input, o leitor de tela não anunciará "Phone".

---

#### ✅ Exemplo 16 — CONFIRMED (campos de grupo sem legend)

```tsx
// src/components/PaymentMethod.tsx
// Selector: fieldset.payment-options
// Message: "Group of related form controls does not have a group label"
// WCAG: 1.3.1

<fieldset className="payment-options">
  {/* ← sem <legend> */}
  <div>
    <input type="radio" id="card" name="payment" value="card" />
    <label htmlFor="card">Credit Card</label>
  </div>
  <div>
    <input type="radio" id="pix" name="payment" value="pix" />
    <label htmlFor="pix">PIX</label>
  </div>
</fieldset>
```

**Decisão: `CONFIRMED`**

**Por quê**: Um `<fieldset>` de botões radio sem `<legend>` priva os usuários de leitores de tela do contexto do grupo. Ao navegar pelos botões, o leitor de tela anunciaria "Credit Card, radio button" e "PIX, radio button" sem indicar que esses são métodos de pagamento. O WCAG 1.3.1 exige que grupos de campos relacionados tenham identificação de grupo.

---

### 5.4 ARIA — Atributos ARIA

**Critérios WCAG**: 4.1.2 (A), 1.3.1 (A)

**O que é**: Atributos ARIA inválidos, desnecessários, mal configurados, ou com valores incorretos que comprometem a semântica para tecnologias assistivas.

**Princípio fundamental (Primeira Regra do ARIA)**: *"Se você pode usar um elemento HTML nativo com a semântica e comportamento que você precisa, use-o."* ARIA deve ser usado apenas quando elementos nativos não são suficientes.

---

#### ✅ Exemplo 17 — CONFIRMED (role inválido)

```tsx
// src/components/Tabs.tsx
// Selector: div.tab-container
// Message: "Element has invalid ARIA role"
// WCAG: 4.1.2

<div
  className="tab-container"
  role="tabs"    // ← "tabs" não existe como role ARIA válido
>
  <button role="tab">Tab 1</button>
  <button role="tab">Tab 2</button>
</div>
```

**Decisão: `CONFIRMED`**

**Por quê**: `role="tabs"` não é um valor válido na especificação WAI-ARIA 1.2. O role correto para o contêiner de abas é `tablist`. A ferramenta está correta — um role inválido é ignorado por tecnologias assistivas, tornando o componente sem semântica acessível.

---

#### ✅ Exemplo 18 — CONFIRMED (aria-expanded sem controle real)

```tsx
// src/components/Dropdown.tsx
// Selector: button.dropdown-trigger
// Message: "Element has aria-expanded but does not control any element"
// WCAG: 4.1.2

<button
  className="dropdown-trigger"
  aria-expanded={isOpen}
  // ← sem aria-controls referenciando o painel expandido
  onClick={toggle}
>
  Options ▼
</button>

{isOpen && (
  <ul className="dropdown-menu">  {/* ← sem id para ser referenciado */}
    <li>Option 1</li>
  </ul>
)}
```

**Decisão: `CONFIRMED`**

**Por quê**: `aria-expanded` deve ser usado em conjunto com `aria-controls` referenciando o elemento que é expandido/contraído. Sem essa referência, o leitor de tela não pode conectar o botão ao conteúdo que ele controla. O padrão correto seria `aria-controls="dropdown-menu-id"` e `id="dropdown-menu-id"` no `<ul>`.

---

#### ❌ Exemplo 19 — FALSE_POSITIVE (ARIA redundante mas não prejudicial)

```tsx
// src/components/Navigation.tsx
// Selector: nav[role="navigation"]
// Message: "Element has redundant ARIA role"
// WCAG: 4.1.2

<nav
  role="navigation"    // ← redundante, <nav> já tem role navigation
  aria-label="Main navigation"
>
  {/* ... */}
</nav>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: Embora `role="navigation"` seja redundante em um elemento `<nav>` (que já possui esse role implícito), esta redundância é **inofensiva** — tecnologias assistivas a ignoram. O elemento é acessível e funcional. O `aria-label` presente fornece contexto adicional valioso. WCAG 4.1.2 proíbe roles *incorretos*, não redundantes.

> **Nota para anotadores**: Atributos ARIA redundantes são um code smell, mas **não constituem uma violação WCAG**. Anote como FALSE_POSITIVE.

---

#### ✅ Exemplo 20 — CONFIRMED (aria-label em elemento não interativo sem role)

```tsx
// src/components/ProductCard.tsx
// Selector: div.card-wrapper
// Message: "aria-label is only valid on interactive elements or landmark roles"
// WCAG: 4.1.2

<div
  className="card-wrapper"
  aria-label="Product: Nike Air Max"   // ← div sem role, aria-label inválido aqui
>
  <img src={product.image} alt={product.name} />
  <h3>{product.name}</h3>
</div>
```

**Decisão: `CONFIRMED`**

**Por quê**: `aria-label` em um `<div>` genérico (sem `role`) não tem efeito semântico — leitores de tela ignoram `aria-label` em elementos não-interativos sem role definido. Se o objetivo era criar um landmark ou widget acessível, seria necessário adicionar um role apropriado (ex: `role="article"` ou `role="group"`).

---

#### ❌ Exemplo 21 — FALSE_POSITIVE (aria-hidden em componente pai com filhos acessíveis)

```tsx
// src/components/Modal.tsx
// Selector: div.modal-overlay
// Message: "aria-hidden element contains focusable content"
// WCAG: 4.1.2

const Modal = ({ isOpen, children }: Props) => (
  <>
    <div
      className="modal-overlay"
      aria-hidden={!isOpen}   // ← aria-hidden=true apenas quando fechado
      onClick={closeModal}
    />
    {isOpen && (
      <div className="modal-content" role="dialog" aria-modal="true">
        {children}
      </div>
    )}
  </>
);
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: `aria-hidden={!isOpen}` significa que quando `isOpen=true`, `aria-hidden` é `false` — o overlay é visível para tecnologias assistivas, mas os botões focáveis estão no `.modal-content`, não no overlay. Quando `isOpen=false`, o modal content não é renderizado (`{isOpen && ...}`), então não há elementos focáveis dentro do `aria-hidden`. A lógica está correta.

---

### 5.5 KEYBOARD — Navegação por Teclado

**Critérios WCAG**: 2.1.1 (A), 2.1.2 (A), 2.4.3 (A)

**O que é**: Elementos interativos que não são alcançáveis ou operáveis apenas pelo teclado excluem usuários que não utilizam mouse.

**Regra geral**: Qualquer funcionalidade disponível por mouse deve ser disponível por teclado.

---

#### ✅ Exemplo 22 — CONFIRMED (div clicável não teclável)

```tsx
// src/components/FileUpload.tsx
// Selector: div.drop-zone
// Message: "Element with onClick is not keyboard accessible"
// WCAG: 2.1.1

<div
  className="drop-zone"
  onClick={handleFileSelect}    // ← clicável
  // ← sem onKeyDown/onKeyPress/tabIndex
  // ← div não recebe foco por padrão
>
  <p>Click or drag to upload</p>
</div>
```

**Decisão: `CONFIRMED`**

**Por quê**: Um `<div>` com `onClick` não é acessível por teclado por padrão: (1) não recebe foco via Tab; (2) não responde a Enter/Space. Para torná-lo acessível, seriam necessários `tabIndex={0}` e handler de teclado (`onKeyDown`), ou preferencialmente substituição por `<button>`.

---

#### ❌ Exemplo 23 — FALSE_POSITIVE (tabIndex correto em componente custom)

```tsx
// src/components/ColorSwatch.tsx
// Selector: div.swatch-item
// Message: "Element with role button is not keyboard accessible"
// WCAG: 2.1.1

<div
  className="swatch-item"
  role="button"
  tabIndex={0}                              // ← recebe foco
  onClick={handleColorSelect}
  onKeyDown={(e) => {                       // ← responde a Enter e Space
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleColorSelect();
    }
  }}
  aria-label={`Select color ${color.name}`}
  aria-pressed={isSelected}
>
  <span style={{ backgroundColor: color.hex }} />
</div>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O elemento tem `role="button"`, `tabIndex={0}` (torna-o focável), e `onKeyDown` que responde a `Enter` e `Space` (as teclas que ativam botões). Implementa corretamente o padrão de keyboard interaction para o role `button` conforme o ARIA Authoring Practices Guide.

---

#### ✅ Exemplo 24 — CONFIRMED (trap de foco em modal sem escape)

```tsx
// src/components/ConfirmDialog.tsx
// Selector: div[role="dialog"]
// Message: "Dialog does not trap focus inside"
// WCAG: 2.1.2

const ConfirmDialog = ({ onConfirm, onCancel }: Props) => (
  <div
    role="dialog"
    aria-modal="true"
    aria-labelledby="dialog-title"
  >
    <h2 id="dialog-title">Confirm Delete</h2>
    <p>Are you sure you want to delete this item?</p>
    <button onClick={onCancel}>Cancel</button>
    <button onClick={onConfirm}>Confirm</button>
    {/* ← sem implementação de focus trap */}
    {/* ← Tab no último botão sai do diálogo para o resto da página */}
  </div>
);
```

**Decisão: `CONFIRMED`**

**Por quê**: Diálogos modais devem manter o foco contido dentro deles enquanto estão abertos (WCAG 2.1.2, pattern de modal no ARIA APG). Sem um *focus trap*, o usuário de teclado pode Tab para sair do modal e interagir com o conteúdo de fundo que deveria estar bloqueado. Isso pode causar ações não intencionais.

---

### 5.6 FOCUS — Indicador de Foco

**Critérios WCAG**: 2.4.7 (AA), 2.4.11 (AA — WCAG 2.2)

**O que é**: Elementos interativos devem ter um indicador visual visível quando recebem foco pelo teclado.

**Armadilha mais comum**: `outline: none` ou `outline: 0` no CSS remove o anel de foco padrão do navegador sem providenciar um substituto.

---

#### ✅ Exemplo 25 — CONFIRMED (outline removido sem substituto)

```tsx
// src/components/ui/Button.tsx
// Selector: button.btn
// Message: "Element has no visible focus indicator"
// WCAG: 2.4.7

// Button.module.css (referenciado neste componente):
// .btn {
//   outline: none;       ← foco removido
//   border: none;
//   background: #0070f3;
// }
// .btn:focus {
//   /* vazio — nenhum substituto */
// }

const Button = ({ children, ...props }: ButtonProps) => (
  <button className={styles.btn} {...props}>
    {children}
  </button>
);
```

**Decisão: `CONFIRMED`**

**Por quê**: O CSS remove o `outline` nativo sem providenciar nenhum indicador visual alternativo no pseudo-seletor `:focus`. Um usuário navegando por teclado não consegue ver qual botão está ativo.

---

#### ❌ Exemplo 26 — FALSE_POSITIVE (outline substituído por box-shadow)

```tsx
// src/components/ui/Input.tsx
// Selector: input.text-input
// Message: "Element has no visible focus indicator"
// WCAG: 2.4.7

// Styles:
// .text-input:focus {
//   outline: none;
//   box-shadow: 0 0 0 3px rgba(0, 112, 243, 0.4);  ← substituto visual presente
//   border-color: #0070f3;                           ← borda colorida adicional
// }
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O `outline: none` é compensado por um `box-shadow` azul de 3px (visualmente equivalente ou superior ao outline nativo) combinado com mudança de `border-color`. Esta é uma técnica de design amplamente utilizada que preserva a acessibilidade com uma aparência mais polida. WCAG 2.4.7 exige um indicador visível, não especificamente `outline`.

---

#### ✅ Exemplo 27 — CONFIRMED (focus apenas em :focus, não :focus-visible — WCAG 2.2)

```tsx
// src/components/ui/NavLink.tsx
// Selector: a.nav-link
// Message: "Focus indicator has insufficient contrast ratio of 1.5:1"
// WCAG: 2.4.11 (WCAG 2.2)

// Styles:
// .nav-link:focus {
//   outline: 1px solid #cccccc;   ← contraste 1.5:1 contra fundo branco
// }
```

**Decisão: `CONFIRMED`** *(se o projeto alvo WCAG 2.2)*

**Por quê**: WCAG 2.2 critério 2.4.11 exige que o indicador de foco tenha contraste de pelo menos 3:1 em relação ao fundo adjacente. Um `outline` cinza claro (`#cccccc`) sobre fundo branco tem contraste de ~1.6:1, insuficiente.

> **Nota para anotadores**: Se o projeto alvo é apenas WCAG 2.1 AA, o critério 2.4.11 não se aplica — neste caso, seria FALSE_POSITIVE. Verifique a configuração `wcag_level` do projeto no catálogo.

---

### 5.7 SEMANTIC — Semântica HTML

**Critérios WCAG**: 1.3.1 (A), 1.3.2 (A), 2.4.6 (AA)

**O que é**: Uso incorreto de elementos HTML que compromete a estrutura semântica — cabeçalhos fora de ordem, landmarks ausentes, listas mal estruturadas.

---

#### ✅ Exemplo 28 — CONFIRMED (hierarquia de cabeçalhos quebrada)

```tsx
// src/pages/ProductDetail.tsx
// Selector: h4.section-title
// Message: "Heading levels should only increase by one"
// WCAG: 1.3.1

const ProductDetail = () => (
  <main>
    <h1>Product Detail</h1>          {/* nível 1 */}
    {/* h2 ausente — pulou do h1 para h4 */}
    <h4 className="section-title">   {/* nível 4 — violação */}
      Specifications
    </h4>
  </main>
);
```

**Decisão: `CONFIRMED`**

**Por quê**: A hierarquia de cabeçalhos salta de H1 para H4, omitindo os níveis H2 e H3. Usuários de leitores de tela frequentemente navegam por cabeçalhos para obter uma visão geral da página. Uma hierarquia quebrada confunde a estrutura lógica do documento (WCAG 1.3.1, técnica H42).

---

#### ❌ Exemplo 29 — FALSE_POSITIVE (div com role adequado como substituto)

```tsx
// src/components/DataGrid.tsx
// Selector: div.data-table
// Message: "Table-like content should use <table> element"
// WCAG: 1.3.1

<div
  className="data-table"
  role="grid"                    // ← role ARIA de grid
  aria-label="User accounts"
  aria-rowcount={totalRows}
>
  <div role="row" aria-rowindex={1}>
    <div role="columnheader" aria-sort="ascending">Name</div>
    <div role="columnheader">Email</div>
    <div role="columnheader">Role</div>
  </div>
  {rows.map((row, i) => (
    <div role="row" key={row.id} aria-rowindex={i + 2}>
      <div role="gridcell">{row.name}</div>
      <div role="gridcell">{row.email}</div>
      <div role="gridcell">{row.role}</div>
    </div>
  ))}
</div>
```

**Decisão: `FALSE_POSITIVE`**

**Por quê**: O componente usa o padrão WAI-ARIA `grid` com roles `row`, `columnheader`, e `gridcell`. Este padrão é válido para grades interativas (onde `<table>` seria semanticamente incorreto por implicar dados tabulares estáticos). A implementação segue o ARIA Authoring Practices Guide para o padrão Grid.

---

#### ✅ Exemplo 30 — CONFIRMED (lista não semântica)

```tsx
// src/components/CategoryMenu.tsx
// Selector: div.categories
// Message: "List-like content is not using semantic list elements"
// WCAG: 1.3.1

<div className="categories">
  <div className="category-item">Electronics</div>
  <div className="category-item">Books</div>
  <div className="category-item">Clothing</div>
  {/* ← deveria ser <ul><li>...</li></ul> */}
</div>
```

**Decisão: `CONFIRMED`**

**Por quê**: Uma série de itens relacionados (categorias de produtos) deveria usar `<ul>`/`<li>` para expressar semanticamente que são uma lista. Leitores de tela anunciam o número de itens em uma lista ("lista com 3 itens"), informação valiosa para navegação. Usando apenas `<div>`, essa estrutura é invisível para tecnologias assistivas.

---

## 6. Casos Limítrofes e Ambíguos

### 6.1 Componentes com props computadas em runtime

Quando valores relevantes são computados em tempo de execução:

```tsx
<button
  aria-disabled={someCondition ? "true" : "false"}
  aria-label={computedLabel}
/>
```

Se `someCondition` e `computedLabel` não são visíveis no trecho:
→ Use **UNCERTAIN** e documente que a decisão depende do estado em runtime.

---

### 6.2 Bibliotecas de componentes de terceiros

```tsx
// Usando componente externo
import { DatePicker } from '@external-library/ui';

<DatePicker
  label="Birth date"
  onChange={handleChange}
/>
```

**Regra**: Se a **API do componente** recebe `label`, `aria-label`, ou props de acessibilidade equivalentes e elas estão preenchidas, presuma que a biblioteca as usa corretamente.
→ Se props de acessibilidade estão ausentes e não são opcionais: **CONFIRMED**.
→ Se não é possível saber: **UNCERTAIN**, note que depende da implementação interna da biblioteca.

---

### 6.3 Contexto de renderização condicional

```tsx
const Alert = ({ message, type }: Props) => {
  if (!message) return null;

  return (
    <div
      role="alert"
      className={`alert alert-${type}`}
    >
      {message}
    </div>
  );
};
```

Se o finding é sobre `role="alert"` — este padrão é válido para anúncios dinâmicos.
→ **FALSE_POSITIVE** para findings sobre ausência de texto alternativo (há `{message}`).
→ Avalie apenas o que está **presente** no código, não hipóteses sobre props.

---

### 6.4 Imagens de fundo via CSS

Imagens definidas via `background-image` CSS não são acessíveis a leitores de tela por definição — mas se são decorativas, isso é correto.

**Regra**:
- Imagem de fundo **decorativa** → sem problema (CSS background images são ignoradas por AT)
- Imagem de fundo **informativa** que deveria ter texto alternativo → **CONFIRMED** (o padrão correto seria usar `<img>` com `alt`)

---

### 6.5 tabIndex negativo

```tsx
<div tabIndex={-1} ref={alertRef} role="alert">
  {message}
</div>
```

`tabIndex={-1}` **não** remove o elemento do fluxo de acessibilidade — apenas o remove da ordem de Tab sequencial. O elemento ainda pode receber foco via `element.focus()` programaticamente, o que é um padrão válido para gerenciar foco em dialogs e alertas.
→ Findings sobre "elemento não focável" em elementos com `tabIndex={-1}` = **FALSE_POSITIVE**.

---

### 6.6 Atributos ARIA em Fragments ou portais

```tsx
// Ferramenta reportou finding em React.Fragment
<>
  <h1>Title</h1>
  <p>Content</p>
</>
```

React Fragments não geram elementos DOM. Findings sobre ARIA em fragments são invariavelmente artefatos de parsing da ferramenta.
→ **FALSE_POSITIVE** sempre.

---

## 7. Erros Comuns de Anotadores

### Erro 1 — Confundir "código ruim" com "violação WCAG"

❌ **Errado**: Anotar como CONFIRMED porque o código parece mal escrito ou difícil de manter.

✅ **Correto**: Anotar como CONFIRMED apenas quando há uma violação verificável de um critério WCAG específico.

---

### Erro 2 — Ignorar `aria-hidden` em elementos aninhados

❌ **Errado**: Ver `aria-hidden="true"` e automaticamente anotar como FALSE_POSITIVE.

✅ **Correto**: Verificar se o `aria-hidden` está no elemento correto e se não há conteúdo interativo (focável) dentro do elemento oculto. `aria-hidden` em elemento com filhos focáveis é uma **violação** (WCAG 4.1.2).

---

### Erro 3 — Desconsiderar o critério WCAG informado

❌ **Errado**: Avaliar genericamente "parece acessível" sem verificar especificamente o critério WCAG indicado.

✅ **Correto**: Identificar o critério no campo WCAG, consultar a seção 5 correspondente, e verificar especificamente aquele requisito.

---

### Erro 4 — Marcar como UNCERTAIN por excesso de cautela

❌ **Errado**: Usar UNCERTAIN sempre que o caso parece um pouco complexo.

✅ **Correto**: UNCERTAIN apenas quando a informação necessária para a decisão genuinamente não está disponível no código apresentado. Se você pode fazer um julgamento razoável, faça-o.

---

### Erro 5 — Avaliar texto de `placeholder` como suficiente para label

❌ **Errado**: Ver `placeholder="Email"` e concluir que o campo tem label.

✅ **Correto**: Placeholder **nunca** substitui um label para fins do WCAG 1.3.1. Mesmo que visualmente pareça adequado, ele desaparece ao digitar e não é anunciado consistentemente por leitores de tela.

---

### Erro 6 — Avaliar conformidade AAA quando o target é AA

❌ **Errado**: Anotar como CONFIRMED uma razão de contraste de 4:1 em texto normal (passa AA, falha AAA).

✅ **Correto**: Verificar sempre a configuração `wcag_level` do projeto. O padrão deste corpus é **WCAG 2.1 AA**. Critérios AAA não são exigidos.

---

## 8. Protocolo de Reconciliação de Discordâncias

Quando dois anotadores atribuem rótulos diferentes ao mesmo finding:

### Passo 1 — Identificação automática

O script `annotate.py` identifica automaticamente todos os findings onde `annotator_1_label ≠ annotator_2_label` e produz um relatório de discordâncias.

### Passo 2 — Reunião de reconciliação estruturada

Os dois anotadores se reúnem (presencialmente ou por videoconferência) para revisar cada discordância:

**Formato da reunião**:
1. Anotador A apresenta seu raciocínio (máx. 2 minutos)
2. Anotador B apresenta seu raciocínio (máx. 2 minutos)
3. Discussão e tentativa de consenso (máx. 5 minutos)
4. Se chegarem a consenso: registrar rótulo acordado
5. Se persistir discordância: escalar para árbitro (ver Passo 3)

**Documentação**: Para cada discordância reconciliada, o anotador que mudou de posição deve registrar a justificativa na nota do finding.

### Passo 3 — Arbitragem por especialista externo

Se após a reunião os anotadores mantiverem posições divergentes:

- O finding é submetido a um **terceiro especialista em acessibilidade** (com certificação CPACC ou WAS, ou auditor de acessibilidade sênior).
- O especialista avalia o finding de forma cega (sem ver os rótulos dos anotadores) e emite um parecer com justificativa escrita.
- O parecer do especialista é definitivo.

### Passo 4 — Findings irresolvíveis

Se nem o árbitro conseguir emitir parecer claro (ex.: depende de comportamento dinâmico não inferível do código estático):
- O finding recebe `ground_truth_label = UNCERTAIN`
- É excluído do conjunto de avaliação primário
- Registrado em apêndice como "finding de alta ambiguidade"

---

## 9. Calibração e Exercícios de Treinamento

Antes de iniciar a anotação oficial, cada anotador deve completar um **exercício de calibração** de 30 findings com gabarito pré-definido.

### Conjunto de calibração

Execute o seguinte comando para acessar o conjunto de calibração:

```bash
python dataset/scripts/annotate.py \
  --catalog dataset/catalog/projects.yaml \
  --project calibration__set \
  --annotator <seu_id>
```

### Critério de aprovação

O anotador só é autorizado a iniciar a anotação oficial quando atingir:
- **Concordância com o gabarito ≥ 80%** (24/30 findings corretos)
- **Zero erros em categorias críticas**: não pode rotular como FALSE_POSITIVE nenhum dos 10 findings CONFIRMED "óbvios" do gabarito (alt ausente, label ausente, outline sem substituto)

### Sessões de alinhamento periódico

A cada 500 findings anotados, os dois anotadores reveem juntos uma amostra aleatória de 20 findings e comparam seus raciocínios para detectar derivação na interpretação das diretrizes (*annotation drift*).

---

## 10. Referências Rápidas WCAG

### Critérios cobertos por este corpus

| Critério | Título | Nível | Tipo de Issue |
|----------|--------|-------|---------------|
| 1.1.1 | Non-text Content | A | `alt_text` |
| 1.3.1 | Info and Relationships | A | `semantic`, `label` |
| 1.3.2 | Meaningful Sequence | A | `semantic` |
| 1.4.3 | Contrast (Minimum) | AA | `contrast` |
| 1.4.6 | Contrast (Enhanced) | AAA | `contrast` |
| 1.4.11 | Non-text Contrast | AA | `contrast` |
| 2.1.1 | Keyboard | A | `keyboard` |
| 2.1.2 | No Keyboard Trap | A | `keyboard` |
| 2.4.3 | Focus Order | A | `focus`, `keyboard` |
| 2.4.6 | Headings and Labels | AA | `semantic`, `label` |
| 2.4.7 | Focus Visible | AA | `focus` |
| 2.4.11 | Focus Appearance | AA (WCAG 2.2) | `focus` |
| 3.3.2 | Labels or Instructions | A | `label` |
| 4.1.2 | Name, Role, Value | A | `aria`, `label` |

### Ferramentas de referência para verificação

| Ferramenta | URL | Uso |
|------------|-----|-----|
| WebAIM Contrast Checker | https://webaim.org/resources/contrastchecker/ | Verificar razões de contraste |
| axe DevTools | Extensão Chrome/Firefox | Verificar ARIA e semântica |
| NVDA (Windows) | https://www.nvaccess.org/ | Testar com leitor de tela real |
| VoiceOver (macOS) | Cmd + F5 | Testar com leitor de tela real |
| WAI-ARIA APG | https://www.w3.org/WAI/ARIA/apg/ | Padrões de design acessíveis |
| WCAG Quick Ref | https://www.w3.org/WAI/WCAG21/quickref/ | Referência dos critérios |

---

## Apêndice — Tabela de Decisão Rápida

| Situação | Decisão | Justificativa |
|----------|---------|---------------|
| `alt` ausente em `<img>` informativa | CONFIRMED | WCAG 1.1.1 |
| `alt=""` em `<img>` decorativa | FALSE_POSITIVE | Técnica correta H67 |
| `alt` presente mas genérico ("image", "photo") | CONFIRMED | Não transmite informação |
| `aria-hidden="true"` em elemento decorativo | FALSE_POSITIVE | Uso correto |
| `aria-hidden="true"` com filho focável | CONFIRMED | WCAG 4.1.2 |
| `outline: none` sem substituto | CONFIRMED | WCAG 2.4.7 |
| `outline: none` com `box-shadow` substituto | FALSE_POSITIVE | Requisito cumprido |
| Contraste < 4.5:1 em texto normal | CONFIRMED | WCAG 1.4.3 |
| Contraste < 4.5:1 em texto ≥ 18pt ou ≥ 14pt bold | FALSE_POSITIVE | Limite é 3:1 |
| Contraste 3:1–4.5:1 em texto grande | FALSE_POSITIVE | WCAG 1.4.3 texto grande |
| `<div onClick>` sem `tabIndex` e `onKeyDown` | CONFIRMED | WCAG 2.1.1 |
| `role="button"` + `tabIndex={0}` + `onKeyDown` correto | FALSE_POSITIVE | Implementação válida |
| `placeholder` como único label | CONFIRMED | WCAG 1.3.1 |
| `aria-label` preenchido no input | FALSE_POSITIVE | Label ARIA válido |
| `aria-labelledby` apontando ID existente | FALSE_POSITIVE | Label ARIA válido |
| `aria-labelledby` com ID inexistente | CONFIRMED | Referência quebrada |
| Role ARIA inválido (não existe na spec) | CONFIRMED | WCAG 4.1.2 |
| Role ARIA redundante (ex: `nav role="navigation"`) | FALSE_POSITIVE | Inofensivo |
| Hierarquia de headings com salto (H1 → H4) | CONFIRMED | WCAG 1.3.1 |
| Cores via variáveis CSS não visíveis no trecho | UNCERTAIN | Valor não verificável |
| `alt` dinâmico via prop: `alt={item.desc \|\| ""}` | UNCERTAIN | Depende do dado em runtime |

---

*Este guia deve ser tratado como documento vivo. Casos não cobertos aqui devem ser levados à reunião de alinhamento e, se recorrentes, incorporados ao guia na próxima versão.*

**Contato para dúvidas**: Registrar no canal de comunicação do projeto com o prefixo `[ANNOTATION]`.
