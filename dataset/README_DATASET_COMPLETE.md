# Estratégias para a Construção do Dataset de Benchmark em Acessibilidade Web Automatizada

## *A Stratified, Multi-Source Corpus of Real-World WCAG Violations for Evaluating Automated Detection and Remediation Systems*

---

**Versão do documento**: 1.0
**Status**: Normativo
**Projeto**: a11y-autofix — Automated Accessibility Remediation via LLM Agents
**Última revisão**: 2025-01

---

## Resumo

Este documento descreve em profundidade as estratégias metodológicas empregadas na construção do corpus de benchmark **a11y-autofix**, uma coleção estruturada de projetos React/TypeScript de código aberto sistematicamente amostrados do GitHub com o propósito de avaliar sistemas de detecção e remediação automática de violações de acessibilidade segundo as *Web Content Accessibility Guidelines* (WCAG) 2.1/2.2. A metodologia fundamenta-se em práticas consolidadas da engenharia de software empírica (Wohlin et al., 2012), em protocolos de construção de benchmarks para reparo automático de programas (Just et al., 2014; Le Goues et al., 2015), e na metodologia de avaliação de conformidade de acessibilidade web do W3C (Abou-Zahra, 2008; W3C/WAI, 2014). O documento detalha cada fase do pipeline de construção — descoberta, triagem, captura de instantâneos (*snapshotting*), varredura multi-ferramenta, anotação de verdade fundamental (*ground truth*) e validação de qualidade —, apresentando os critérios formais que governam cada decisão, os modelos de dados que representam os artefatos produzidos, as métricas de qualidade que determinam a aptidão do corpus para publicação acadêmica, e as análises estatísticas que suportam as questões de pesquisa que o dataset foi projetado para responder.

---

## Sumário

1. [Motivação e Objetivos de Pesquisa](#1-motivação-e-objetivos-de-pesquisa)
2. [Fundamentação Teórica e Revisão de Metodologias](#2-fundamentação-teórica-e-revisão-de-metodologias)
3. [Framework de Validade](#3-framework-de-validade)
4. [Definição da População-Alvo](#4-definição-da-população-alvo)
5. [Estratégia de Descoberta via GitHub API](#5-estratégia-de-descoberta-via-github-api)
6. [Critérios de Inclusão e Exclusão](#6-critérios-de-inclusão-e-exclusão)
7. [Design de Estratificação](#7-design-de-estratificação)
8. [Protocolo de Captura de Instantâneos](#8-protocolo-de-captura-de-instantâneos)
9. [Protocolo de Varredura Multi-Ferramenta](#9-protocolo-de-varredura-multi-ferramenta)
10. [Protocolo de Anotação de Verdade Fundamental](#10-protocolo-de-anotação-de-verdade-fundamental)
11. [Métricas de Qualidade do Corpus](#11-métricas-de-qualidade-do-corpus)
12. [Análises Estatísticas](#12-análises-estatísticas)
13. [Modelos de Dados e Representação Formal](#13-modelos-de-dados-e-representação-formal)
14. [Reprodutibilidade e Pacote de Replicação](#14-reprodutibilidade-e-pacote-de-replicação)
15. [Considerações Éticas, Legais e de Privacidade](#15-considerações-éticas-legais-e-de-privacidade)
16. [Ameaças à Validade](#16-ameaças-à-validade)
17. [Referências Bibliográficas](#17-referências-bibliográficas)

---

## 1. Motivação e Objetivos de Pesquisa

### 1.1 Contexto e Relevância

A acessibilidade digital constitui, tanto nos aspectos ético quanto legal, um requisito fundamental para o desenvolvimento de sistemas computacionais. Estimativas do *World Health Organization* (WHO, 2023) indicam que aproximadamente 1,3 bilhões de pessoas no mundo vivem com alguma forma de deficiência, representando cerca de 16% da população global. No contexto jurídico-normativo, múltiplos instrumentos legais — incluindo a *Section 508* do *Rehabilitation Act* (EUA, 1998), a Diretiva 2016/2102/EU (União Europeia, 2016), a norma EN 301 549 (ETSI, 2021) e, no contexto nacional brasileiro, a Lei Brasileira de Inclusão (Lei nº 13.146/2015) e a ABNT NBR 17060:2022 — exigem que produtos digitais de uso público atendam a padrões mínimos de acessibilidade.

Apesar da crescente pressão regulatória, estudos empíricos demonstram que a prevalência de violações de acessibilidade em aplicações web permanece elevada. Alshayban et al. (2020), em um estudo abrangente sobre aplicativos Android, identificaram violações de acessibilidade em 71% das 1.000 aplicações analisadas. Bajammal e Mesbah (2021), focando em componentes web React, Angular e Vue, constataram que a maioria dos componentes de UI populares apresenta pelo menos uma violação de acessibilidade detectável por ferramentas automatizadas. Estes resultados evidenciam a necessidade de ferramentas e metodologias que possam escalar a detecção e, idealmente, automatizar a correção dessas violações.

### 1.2 Problema Central

A avaliação rigorosa de sistemas de detecção e remediação automática de acessibilidade exige um corpus de *benchmark* que satisfaça requisitos metodológicos estritos: (i) diversidade de domínio de aplicação suficiente para sustentar afirmações de generalidade; (ii) rastreabilidade das violações a critérios normativos específicos das WCAG; (iii) verdade fundamental (*ground truth*) validada com mensuração formal de concordância inter-anotadores; (iv) reprodutibilidade total, de modo que qualquer pesquisador possa replicar os resultados exatamente; e (v) tamanho suficiente para sustentar inferências estatísticas com poder adequado.

Nenhum benchmark público existente para acessibilidade web satisfaz simultaneamente todos esses requisitos no contexto de frameworks JavaScript modernos, particularmente React com TypeScript, que constitui hoje o paradigma dominante no desenvolvimento front-end de larga escala.

### 1.3 Questões de Pesquisa

O dataset foi projetado para suportar as seguintes questões de pesquisa:

| ID   | Questão de Pesquisa | Variáveis |
|------|---------------------|-----------|
| **RQ1** | Protocolos de consenso multi-ferramenta produzem taxas de falso positivo estatisticamente menores do que abordagens de ferramenta única para detecção de violações WCAG em componentes React? | Nível de consenso × Taxa de FP |
| **RQ2** | Agentes de reparo baseados em LLM apresentam taxas de sucesso estatisticamente diferentes entre tipos de violações WCAG (contraste, ARIA, teclado, rótulo, semântica, texto alternativo, foco)? | Tipo de violação × Taxa de reparo |
| **RQ3** | Há diferença estatisticamente significativa na taxa de reparo entre modelos de linguagem especializados em código (Qwen2.5-Coder, DeepSeek-Coder, CodeLlama) e modelos de propósito geral (Llama 3.1)? | Modelo × Taxa de reparo |
| **RQ4** | Sob quais condições um agente autônomo (OpenHands, SWE-agent) supera prompting direto de LLM para reparo de acessibilidade? | Complexidade da violação × Tipo de agente |
| **RQ5** | Resultados obtidos em um benchmark sintético generalizam-se para uma população de codebases React reais de produção? | Validade externa |

---

## 2. Fundamentação Teórica e Revisão de Metodologias

### 2.1 Benchmarks em Engenharia de Software Empírica

A construção de benchmarks para avaliação de ferramentas de engenharia de software é uma prática estabelecida que remonta ao trabalho seminal de Hutchins et al. (1994) sobre critérios de teste de mutação. A seguir descrevem-se as principais obras que fundamentam o método adotado neste trabalho:

#### 2.1.1 Defects4J (Just et al., 2014)

O Defects4J representa o padrão de referência metodológico para benchmarks de reparo automático de programas. Sua contribuição central para a presente metodologia é a noção de **isolamento reproduzível de defeitos**: cada entrada do corpus consiste em um par (versão defeituosa, versão corrigida) ancorado a um *commit* específico do sistema de controle de versão, eliminando a ambiguidade sobre o estado exato do código avaliado. Adaptamos este princípio através do protocolo de **pinagem de *commit*** (seção 8): cada projeto do corpus é ancorado a um SHA-1 específico, garantindo que avaliações futuras operem sobre exatamente o mesmo código.

A segunda contribuição metodológica do Defects4J é o conjunto de critérios de inclusão explícitos e documentados (pelo menos 1.000 linhas de código, presença de suíte de testes, defeito reproduzível). Estes critérios estabeleceram o modelo formal de criação de critérios de triagem que adotamos nas seções IC1–IC7.

#### 2.1.2 ManyBugs e IntroClass (Le Goues et al., 2015)

O benchmark ManyBugs ampliou a metodologia do Defects4J para a linguagem C, introduzindo dois conceitos metodológicos relevantes: (i) a importância da **diversidade de origem dos defeitos** (os bugs foram coletados de múltiplos projetos independentes, não de um único repositório); e (ii) a diferença entre **defeitos naturalistas** (*naturally occurring bugs*, extraídos do histórico de desenvolvimento real) e **defeitos sintéticos** (criados por mutação). O corpus a11y-autofix adota exclusivamente a abordagem naturalista: todas as violações de acessibilidade são detectadas em código-fonte produzido por desenvolvedores humanos em projetos de produção.

#### 2.1.3 Benchmarks de Acessibilidade Web

Bajammal e Mesbah (2021) constituem a referência mais próxima ao presente trabalho no domínio de acessibilidade web. Seu dataset consiste em 25 projetos de componentes de interface em frameworks JavaScript populares, analisados com ferramentas automatizadas axe-core e Lighthouse. As limitações identificadas em seu trabalho — cobertura restrita a componentes de UI e não a aplicações completas; ausência de anotação manual de verdade fundamental; falta de estratificação por domínio de aplicação — motivaram diretamente as escolhas metodológicas deste corpus.

Alshayban et al. (2020), ao estudar 1.000 aplicativos Android, estabeleceram um padrão metodológico para estudos de acessibilidade em escala: uso de ferramentas automatizadas para triagem inicial, seguido de revisão manual por especialistas para estabelecer verdade fundamental, com cômputo de concordância inter-anotadores (Cohen's κ) como indicador de qualidade. Este método de duas fases é adotado integralmente na seção 10.

#### 2.1.4 Padrões de Qualidade em Repositórios de Dados de MSR

Gonzalez-Barahona e Robles (2012) estabeleceram critérios de qualidade para estudos empíricos baseados em dados extraídos de repositórios de desenvolvimento: rastreabilidade, reprodutibilidade, e completude de metadados. Estes critérios são operacionalizados nas métricas de qualidade QM1–QM8 (seção 11). Kalliamvakou et al. (2014), em estudo sobre os "perigos" de estudos com dados do GitHub, documentaram vieses sistemáticos que afetam amostras não controladas de repositórios: predominância de projetos pessoais e de aprendizado, projetos inativos, repositórios duplicados e projetos sem código-fonte significativo. Os critérios de exclusão EC1–EC7 foram formulados diretamente para mitigar os vieses identificados por esses autores.

### 2.2 Padrões Normativos de Acessibilidade

#### 2.2.1 WCAG 2.1 e 2.2

As *Web Content Accessibility Guidelines* versão 2.1 (W3C, 2018) e 2.2 (W3C, 2023) constituem o padrão técnico internacional de referência para acessibilidade de conteúdo web. As WCAG organizam-se em torno de quatro princípios fundamentais, memoráveis pelo acrônimo POUR:

- **Perceptível** (*Perceivable*, Princípio 1): Informações e componentes de interface devem ser apresentáveis aos usuários de formas que eles possam perceber (Critérios 1.1–1.4).
- **Operável** (*Operable*, Princípio 2): Componentes de interface e navegação devem ser operáveis por todos os usuários, incluindo aqueles que utilizam apenas teclado ou tecnologias assistivas (Critérios 2.1–2.5).
- **Compreensível** (*Understandable*, Princípio 3): Informações e operação da interface devem ser compreensíveis (Critérios 3.1–3.3).
- **Robusto** (*Robust*, Princípio 4): Conteúdo deve ser robusto o suficiente para ser interpretado por uma ampla variedade de agentes de usuário, incluindo tecnologias assistivas (Critério 4.1).

Cada princípio contém *guidelines*, e cada *guideline* contém **critérios de sucesso** classificados em três níveis de conformidade: A (mínimo obrigatório), AA (padrão adotado pela maioria das legislações) e AAA (nível mais elevado). O corpus a11y-autofix foca no nível WCAG 2 AA por ser o nível exigido pela maioria das legislações de acessibilidade vigentes.

#### 2.2.2 WCAG-EM (Metodologia de Avaliação de Conformidade)

O W3C/WAI (2014) publicou a *Website Accessibility Conformance Evaluation Methodology* (WCAG-EM), um processo estruturado em cinco etapas para avaliação de conformidade de sítios web completos: (1) definir o escopo da avaliação; (2) explorar o sítio avaliado; (3) selecionar uma amostra representativa; (4) auditar a amostra selecionada; (5) relatar os resultados. A fase de varredura do corpus (seção 9) adapta as etapas 3 e 4 da WCAG-EM ao contexto de avaliação em larga escala de projetos de código aberto.

### 2.3 Metodologias de Amostragem em Estudos MSR

A amostragem de repositórios de software para estudos empíricos é um problema metodológico com literatura específica. Heckman e Williams (2011) demonstraram que amostras de conveniência do GitHub (os projetos mais populares por número de *stars*) introduzem viés de seleção significativo, pois projetos populares frequentemente possuem práticas de desenvolvimento mais maduras que a média da população. Para mitigar este viés, este corpus emprega **amostragem estratificada** (Cochran, 1977) — uma técnica que divide a população em subgrupos (*strata*) mutuamente exclusivos e exaustivos, e amostra de cada subgrupo de forma independente — ao invés de amostragem por conveniência baseada em popularidade.

---

## 3. Framework de Validade

A construção do dataset segue o *framework* de validade proposto por Wohlin et al. (2012), que distingue quatro categorias de ameaças à validade em estudos experimentais de engenharia de software:

### 3.1 Validade de Construto

A **validade de construto** (*construct validity*) diz respeito à correspondência entre as operacionalizações empregadas no estudo e os conceitos teóricos que se pretende medir.

**Ameaças identificadas:**
- *Medição de acessibilidade*: Ferramentas automatizadas capturam apenas uma fração (estimada entre 30–40%) de todas as violações WCAG existentes (Vigo et al., 2013). Violações que requerem avaliação subjetiva de conteúdo — como a adequação semântica de um texto alternativo — não são detectáveis automaticamente.
- *Definição de "reparo bem-sucedido"*: A verificação automatizada de que um reparo eliminou a violação original sem introduzir novas violações não é equivalente a uma avaliação humana completa de conformidade.

**Mitigações implementadas:**
- Uso de quatro ferramentas complementares (pa11y, axe-core, Lighthouse, Playwright+axe) para aumentar a cobertura de critérios detectáveis.
- Definição explícita e formalizada de "reparo bem-sucedido" como a eliminação da violação original (verificada por re-execução das ferramentas) sem introdução de novas violações no mesmo arquivo.
- Documentação das limitações de detecção automatizada na seção 16.

### 3.2 Validade Interna

A **validade interna** (*internal validity*) refere-se à capacidade de estabelecer relações causais sem confundimento por variáveis espúrias.

**Ameaças identificadas:**
- *Variabilidade de ambiente*: Diferenças na versão do Node.js, versões de ferramentas de acessibilidade, ou configurações de sistema entre execuções podem produzir resultados diferentes.
- *Contaminação de dados*: Se o LLM foi pré-treinado em código dos projetos do corpus, sua capacidade de reparo pode ser inflacionada.

**Mitigações implementadas:**
- **Pinagem de versões**: Todas as ferramentas de varredura são executadas em versões exatas registradas nos metadados do dataset (`tool_versions` na `FindingSummary`).
- **Pinagem de *commits***: Cada projeto é avaliado exatamente no SHA-1 registrado em `pinned_commit`, eliminando variação decorrente de atualizações do código-fonte.
- **Seed aleatório fixo**: Operações que envolvam aleatoriedade (como seleção de arquivos para anotação) utilizam semente fixa documentada.

### 3.3 Validade Externa

A **validade externa** (*external validity*) diz respeito à generalização dos resultados para além da amostra estudada.

**Ameaças identificadas:**
- *Viés de framework*: O corpus foca em React/TypeScript; resultados podem não generalizar para Angular, Vue, Svelte ou outros frameworks.
- *Viés de popularidade*: Projetos no GitHub não são representativos de todos os projetos React em produção (muitos são privados ou hospedados em outros sistemas).
- *Viés de domínio*: Sete domínios de aplicação cobrem um subconjunto da diversidade real de aplicações web.

**Mitigações implementadas:**
- Estratificação em três dimensões (domínio × tamanho × popularidade) para maximizar a diversidade da amostra.
- Mínimo de 5 projetos por estrato de domínio.
- Documentação explícita do escopo de generalização reivindicável.

### 3.4 Validade de Conclusão

A **validade de conclusão** (*conclusion validity*) refere-se à confiabilidade das inferências estatísticas extraídas dos dados.

**Ameaças identificadas:**
- *Poder estatístico insuficiente*: Amostras pequenas podem não detectar diferenças reais entre condições experimentais.
- *Teste de hipóteses múltiplas*: A avaliação de múltiplos modelos e múltiplos tipos de violação aumenta a probabilidade de falsos positivos estatísticos.

**Mitigações implementadas:**
- Tamanho mínimo do corpus: N ≥ 50 projetos, determinado por análise de poder estatístico (α = 0.05, β = 0.20, tamanho de efeito médio d = 0.5, conforme Cohen, 1988).
- Mínimo de 3 execuções por modelo por projeto (`runs_per_model ≥ 3`) para estimativa de variância.
- Correção de Bonferroni para comparações múltiplas.

---

## 4. Definição da População-Alvo

### 4.1 Universo e Escopo

A **população-alvo** (*target population*) do presente estudo é o conjunto de todos os repositórios GitHub publicamente acessíveis que atendem simultaneamente aos seguintes critérios constitutivos:

1. **Linguagem primária**: TypeScript ou JavaScript (determinado pelo campo `language` da API GitHub, que reflete a linguagem com maior número de bytes de código).
2. **Framework**: React ≥ 16.0, determinado pela presença de `"react"` como dependência direta em `package.json`, excluindo dependências de framework distintos (Angular, Vue).
3. **Atividade**: Pelo menos um *commit push* nos 24 meses anteriores à data de coleta, indicando que o projeto não está abandonado e que o código é representativo de práticas contemporâneas.
4. **Tamanho**: Entre 10 e 2.000 arquivos `.tsx`/`.jsx` nos caminhos de varredura declarados, excluindo arquivos gerados, de teste e de *storybook*. Este intervalo exclui projetos triviais (menos de 10 componentes) e projetos excessivamente grandes onde a varredura completa seria inviável.
5. **Relevância de acessibilidade**: O repositório contém componentes de UI renderizados em navegador (*browser-rendered UI*), excluindo projetos exclusivamente de *backend*, bibliotecas utilitárias sem interface gráfica, ou ferramentas de linha de comando.

### 4.2 Frame de Amostragem

O **frame de amostragem** (*sampling frame*) é o subconjunto da população-alvo que é efetivamente alcançável pelo processo de coleta adotado. No contexto deste trabalho, o frame é delimitado pelas capacidades e restrições da *GitHub Search API* (REST v3):

- A API retorna no máximo 1.000 resultados por consulta de busca.
- A taxa de requisições autenticadas é de 30 requisições/minuto para a *Search API*.
- A API não fornece busca exaustiva de toda a plataforma; os resultados são influenciados por fatores como popularidade, data de atualização e relevância de texto.

A discrepância entre população-alvo e frame de amostragem constitui uma ameaça à validade externa documentada na seção 16. Para mitigar este viés, empregamos múltiplas consultas por estrato de domínio com variações de termos (seção 5.2), de modo a ampliar a cobertura do frame.

---

## 5. Estratégia de Descoberta via GitHub API

### 5.1 Arquitetura do Cliente HTTP

A descoberta de projetos é implementada na classe `GitHubDiscovery` (arquivo `dataset/scripts/discover.py`), um cliente HTTP construído sobre a biblioteca `httpx` (versão ≥ 0.24) com as seguintes características arquiteturais:

**Autenticação**: Todas as requisições incluem o cabeçalho `Authorization: Bearer <token>` e `X-GitHub-Api-Version: 2022-11-28`, maximizando o limite de taxa para 30 requisições/minuto (vs. 10/minuto sem autenticação) e garantindo compatibilidade com a versão específica da API.

**Recuo exponencial com *jitter*** (*exponential backoff with jitter*): Ao receber respostas HTTP 429 (*Too Many Requests*) ou 403 (*Forbidden*), o cliente lê o campo de cabeçalho `X-RateLimit-Reset` — que contém o *timestamp* Unix do próximo reset da janela de taxa — e suspende a execução até esse instante mais uma margem de 5 segundos. Na ausência desse cabeçalho, aplica-se uma espera mínima de 60 segundos. Este mecanismo respeita os limites de taxa da API sem recorrer a *polling* agressivo (Kleppmann, 2017).

**Paginação**: A API retorna no máximo 100 itens por página. O cliente itera sobre as páginas automaticamente, respeitando uma pausa de 2 segundos entre páginas para evitar atingir o limite secundário de taxa da API GitHub.

### 5.2 Estratégia de Consulta por Estrato de Domínio

Para cada estrato de domínio de aplicação, foram definidas quatro consultas complementares que exploram diferentes metadados do repositório GitHub:

| Tipo de Consulta | Exemplo | Justificativa |
|-----------------|---------|---------------|
| **Tópico primário** | `topic:ecommerce language:TypeScript stars:>100` | Alta precisão; tópicos são atribuídos explicitamente pelo mantenedor |
| **Tópico secundário** | `topic:storefront language:TypeScript stars:>100` | Amplia cobertura com sinônimos do domínio |
| **Texto livre em descrição** | `react typescript shopping-cart marketplace in:topics` | Captura projetos que não usam tópicos formais |
| **Combinação de termos** | `react ecommerce typescript in:description stars:>200` | Busca em texto livre para projetos menos categorizados |

Esta estratégia multi-consulta reduz o risco de *undercoverage* que ocorreria ao depender de uma única forma de categorização. A tabela completa de consultas por domínio está definida no dicionário `DOMAIN_QUERIES` em `discover.py`.

### 5.3 Deduplicação

Resultados de múltiplas consultas são deduplicados pelo campo `id` do repositório na API GitHub (inteiro único e imutável), não pelo nome — que pode sofrer renomeação. O conjunto `seen_ids` é mantido ao longo de toda a execução da descoberta, incluindo os IDs de entradas já presentes no catálogo, prevenindo a adição de duplicatas mesmo entre execuções de descoberta incrementais.

### 5.4 Gerenciamento Incremental do Catálogo

A descoberta é projetada para ser executada de forma incremental: ao iniciar, o script carrega o catálogo existente e calcula o número de projetos adicionais necessários para atingir a meta de cada estrato (`target - current_count`). Esta abordagem permite retomar a coleta interrompida sem reprocessar projetos já catalogados e facilita a expansão controlada do corpus ao longo do tempo.

---

## 6. Critérios de Inclusão e Exclusão

### 6.1 Arquitetura da Triagem em Duas Fases

A aplicação dos critérios de triagem segue uma arquitetura em duas fases, inspirada no protocolo de seleção de estudos para revisões sistemáticas da literatura (Kitchenham, 2004):

**Fase 1 — Triagem automatizada** (critérios IC1–IC5, EC1–EC4): Aplicada exclusivamente com base nos metadados retornados pela API GitHub, sem necessidade de clonar o repositório. Esta fase processa centenas de candidatos com baixo custo computacional, eliminando a grande maioria dos repositórios inelegíveis de forma eficiente.

**Fase 2 — Triagem manual** (critérios IC6, IC7, EC5–EC7): Aplicada após clonar o repositório superficialmente (*shallow clone*). Requer inspeção do conteúdo do código-fonte e, em alguns casos, julgamento humano sobre a natureza do projeto. Esta fase é mais custosa, mas processa apenas os candidatos que passaram pela Fase 1.

Toda decisão de triagem é registrada no objeto `ScreeningRecord` associado a cada entrada do catálogo, com o critério específico que motivou eventual exclusão e uma justificativa em texto livre. Este registro constitui uma trilha de auditoria completa que permite verificar e contestar decisões individuais de inclusão/exclusão.

### 6.2 Critérios de Inclusão

#### IC1 — Popularidade mínima (Stars ≥ 100)

**Operacionalização**: Campo `stargazers_count` ≥ 100 na resposta da API GitHub.

**Justificativa**: O limiar de 100 *stars* serve como proxy para um nível mínimo de adoção e visibilidade do projeto. Projetos com menos de 100 *stars* representam frequentemente experimentos individuais, projetos de aprendizado ou projetos abandonados precocemente. O limiar de 100 foi adotado por ser suficientemente baixo para incluir projetos relevantes de nicho (como projetos de governo com menor engajamento em plataformas de código aberto) enquanto exclui a cauda longa de repositórios com baixíssima utilização. Este critério é análogo ao critério de tamanho mínimo de suíte de testes do Defects4J (Just et al., 2014), funcionando como indicador de maturidade mínima do projeto.

**Limitação**: Popularidade em plataformas de código aberto é influenciada por fatores não relacionados à qualidade ou representatividade do código (timing de publicação, *hype* tecnológico, marketing). Esta limitação é documentada como ameaça à validade de construto (seção 16).

#### IC2 — Atividade recente (último *push* ≤ 24 meses)

**Operacionalização**: Campo `pushed_at` na API GitHub, convertido para objeto `datetime` com fuso horário UTC e comparado com o instante da coleta menos 730 dias.

**Justificativa**: Projetos sem atividade por mais de 24 meses são considerados inativos e provavelmente não representam práticas contemporâneas de desenvolvimento React. Adicionalmente, projetos inativos podem ter dependências desatualizadas que impeçam a instalação e varredura bem-sucedidas. O limiar de 24 meses é mais permissivo do que o adotado por alguns estudos MSR (12 meses) para acomodar projetos de utilidade específica que podem ter ciclos de desenvolvimento lentos mas ainda serem mantidos.

#### IC3 — Licença de código aberto aprovada pela OSI

**Operacionalização**: Campo `license.spdx_id` na API GitHub pertencente ao conjunto de identificadores SPDX aceitos: `{MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, GPL-2.0, GPL-2.0-only, GPL-2.0-or-later, GPL-3.0, GPL-3.0-only, GPL-3.0-or-later, AGPL-3.0, AGPL-3.0-only, AGPL-3.0-or-later, MPL-2.0, LGPL-2.1, LGPL-3.0, CC0-1.0}`.

**Justificativa**: A inclusão de apenas projetos sob licenças aprovadas pela OSI (*Open Source Initiative*) garante que: (i) o código pode ser clonado, analisado e redistribuído os resultados de análise sem violação de direitos autorais; (ii) o dataset pode ser publicado sem restrições para uso em pesquisa; e (iii) os projetos representam código genuinamente aberto, não apenas código "aberto para visualização" sem direitos de uso.

#### IC4 — Arquivos de componentes React (≥ 10 arquivos `.tsx`/`.jsx`)

**Operacionalização**: Contagem de arquivos correspondendo ao padrão `**/*.{tsx,jsx}` nos caminhos de varredura declarados, excluindo os caminhos de exclusão definidos por projeto (tipicamente `node_modules/`, `dist/`, `build/`, arquivos `.test.tsx`, `.spec.tsx`, `.stories.tsx`).

**Justificativa**: O limiar de 10 componentes garante que o projeto possui substância suficiente para gerar violações de acessibilidade representativas de diferentes tipos. Projetos com menos de 10 componentes tipicamente são bibliotecas mínimas ou demonstrações que não representam a complexidade de aplicações reais. A verificação deste critério requer acesso ao sistema de arquivos e, portanto, é realizada durante a Fase 2 pelo script `snapshot.py`.

#### IC5 — *Buildability* (presença de `package.json`)

**Operacionalização**: Proxy automatizado via campo `language` da API GitHub: aceito se `language ∈ {TypeScript, JavaScript}`. Verificação definitiva durante Fase 2: presença do arquivo `package.json` no diretório raiz ou em um subdiretório de primeiro nível.

**Justificativa**: A presença do `package.json` é condição necessária (mas não suficiente) para que as ferramentas de varredura possam instalar dependências e executar o projeto. A verificação do campo `language` é uma heurística de alta precisão para esta condição, disponível sem clone.

#### IC6 — Código não-gerado (< 30% de arquivos gerados automaticamente)

**Operacionalização**: Heurística implementada em `snapshot.py::is_predominantly_generated()`: varredura do conteúdo das primeiras linhas de cada arquivo `.tsx`/`.jsx` em busca de indicadores de geração automática: comentários `@generated`, `// THIS FILE IS AUTO-GENERATED`, `/* eslint-disable */` com aviso de geração, ou prefixos de ferramentas como `// Generated by Plasmic`, `// [builder.io]`, etc.

**Justificativa**: Código gerado automaticamente por ferramentas de *low-code* ou geradores de tipos não é representativo do código produzido por desenvolvedores humanos e não é um caso de uso-alvo para remediação por agentes LLM. Sua inclusão distorceria as métricas de taxa de reparo.

#### IC7 — Componentes de UI renderizados em navegador

**Operacionalização**: Heurística implementada em `snapshot.py::has_jsx_exports()`: verificação da presença de pelo menos um arquivo `.tsx`/`.jsx` contendo exportações de componentes React com sintaxe JSX (`return (<` ou `return(` seguido de elemento JSX em uma ou duas linhas).

**Justificativa**: Projetos React podem conter código TypeScript que não renderiza interfaces de usuário (ex.: servidores Next.js, scripts de linha de comando em projetos Electron, utilitários de teste). O IC7 garante que o projeto efetivamente contém componentes de interface visual, que são os alvos relevantes para avaliação de acessibilidade.

### 6.3 Critérios de Exclusão

#### EC1 — *Starter*/Template/Boilerplate

**Operacionalização**: Correspondência de padrões de expressão regular no nome do repositório e na descrição: `\b(starter|boilerplate|template|scaffold|create-|skeleton)\b` (case-insensitive).

**Justificativa**: Projetos de *starter* e boilerplate representam pontos de partida genéricos, não aplicações desenvolvidas com casos de uso reais. Suas violações de acessibilidade refletem as escolhas dos criadores do template, não de desenvolvedores de aplicações, e podem distorcer o perfil de violações do corpus. Este critério reflete a recomendação de Kalliamvakou et al. (2014) de excluir repositórios sem desenvolvimento efetivo.

#### EC2 — Projeto de Curso/Aprendizado

**Operacionalização**: Correspondência de padrões no campo `description` e lista de `topics`: `\b(homework|course|tutorial|learning|bootcamp|assignment|practice)\b`.

**Justificativa**: Projetos educacionais frequentemente apresentam código em estágios iniciais de aprendizado, com violações de acessibilidade que refletem falta de conhecimento do estudante, não práticas de desenvolvimento profissional. Sua inclusão enviesa o corpus em direção a violações triviais e facilmente corrigíveis.

#### EC3 — Repositório *Fork*

**Operacionalização**: Campo `fork == true` na API GitHub.

**Justificativa**: *Forks* duplicam código de outros repositórios. A inclusão de um *fork* juntamente com seu repositório de origem introduziria violações idênticas duas vezes no corpus, inflacionando artificialmente certas contagens e violando o princípio de independência das observações.

#### EC4 — Repositório Arquivado

**Operacionalização**: Campo `archived == true` na API GitHub.

**Justificativa**: Repositórios arquivados são congelados por seus mantenedores, sinalizando que o desenvolvimento cessou definitivamente. Além de não representar práticas contemporâneas, repositórios arquivados não podem receber correções, tornando-os inadequados para o objetivo de avaliação de remediação.

#### EC5 — Dependências Privadas Não-Resolúveis

**Operacionalização**: Verificação manual durante Fase 2: tentativa de `npm install --prefer-offline` seguida de inspeção de erros de pacotes `@scope/package` com código de erro 404 ou E403.

**Justificativa**: Ferramentas de varredura como Lighthouse e Playwright requerem que a aplicação seja executável. Projetos com dependências privadas não podem ser instalados sem acesso às credenciais privadas correspondentes, inviabilizando a varredura dinâmica.

#### EC6 — Aplicações Não-Navegador

**Operacionalização**: Inspeção manual do `package.json` e estrutura do projeto: verificação da presença de `react-native`, `electron` como dependências principais, ou ausência de qualquer referência a DOM/browser.

**Justificativa**: React Native e Electron (em seu processo principal) não renderizam HTML para navegadores web. As WCAG aplicam-se especificamente a conteúdo web; ferramentas de avaliação de acessibilidade web (axe, Lighthouse) não são aplicáveis a interfaces nativas.

#### EC7 — Interface Predominantemente Gerada

**Operacionalização**: Ampliação do IC6: projetos onde > 50% dos arquivos de componentes são gerados por ferramentas como Storybook (arquivos `.stories.tsx`), Plasmic, Builder.io ou similares, mesmo que os demais arquivos contenham código humano.

---

## 7. Design de Estratificação

### 7.1 Fundamento Teórico da Estratificação

A amostragem estratificada (Cochran, 1977) é preferível à amostragem aleatória simples quando a população é heterogênea e quando se deseja garantir representatividade em subgrupos específicos. No contexto deste corpus, a amostragem aleatória simples tenderia a sobre-representar projetos de ferramentas de desenvolvedor e dashboards (domínios com alta concentração de projetos TypeScript populares no GitHub) e sub-representar projetos governamentais e de saúde (domínios com menor visibilidade no ecossistema de código aberto).

### 7.2 Dimensão 1: Domínio de Aplicação

O corpus estratifica-se em sete domínios de aplicação, definidos com base na revisão de estudos de acessibilidade web (Lazar et al., 2007; Power et al., 2012) que documentam variações significativas no perfil de violações de acessibilidade entre tipos de aplicação:

| Estrato | Domínio | N-alvo | Justificativa de Inclusão |
|---------|---------|--------|--------------------------|
| D1 | E-commerce / Varejo | 8 | Alta densidade de interações de formulário (IC/EC, checkout, cadastro); violações de rótulos e contraste frequentes (Power et al., 2012) |
| D2 | Governo / Cívico | 6 | Obrigação legal de conformidade WCAG 2.1 AA em muitas jurisdições; público-alvo diverso com alta proporção de usuários com deficiência (Lazar et al., 2007) |
| D3 | Saúde / Médico | 6 | Contexto crítico onde inacessibilidade tem consequências diretas; regulamentação específica (HIPAA, normas de TI em saúde) frequentemente exige acessibilidade |
| D4 | Educação / Aprendizado | 7 | Obrigação legal em instituições públicas; plataformas LMS com diversidade de tipos de conteúdo |
| D5 | Ferramentas de Desenvolvedor | 7 | Alta adoção de React/TypeScript; design systems e bibliotecas de componentes com amplo impacto de downstream |
| D6 | Dashboard / Analytics | 8 | Alta densidade de componentes visuais (gráficos, tabelas) com padrões de acessibilidade específicos (WCAG 1.4.x, 1.3.x) |
| D7 | Social / Comunicação | 8 | Interações em tempo real; conteúdo dinâmico com desafios ARIA específicos (*live regions*, notificações) |

**Total-alvo**: N ≥ 50 projetos, com mínimo de 5 por estrato.

### 7.3 Dimensão 2: Tamanho do Codebase

O tamanho do codebase é operacionalizado pela contagem de arquivos `.tsx`/`.jsx` nos caminhos de varredura:

| Estrato | Faixa | Distribuição-alvo | Justificativa |
|---------|-------|-------------------|---------------|
| S1 — Pequeno | 10–50 arquivos | 30% | Aplicações de pequeno porte e microsites |
| S2 — Médio | 51–300 arquivos | 50% | A maioria das aplicações empresariais de porte médio |
| S3 — Grande | > 300 arquivos | 20% | Plataformas complexas (ex.: Grafana, Kibana) |

A distribuição 30/50/20 reflete aproximadamente a distribuição observada empiricamente em estudos MSR de aplicações React (Mattsson et al., 2021, estimativa baseada em dados do npm).

### 7.4 Dimensão 3: Popularidade do Projeto

| Estrato | Faixa de *Stars* | Distribuição-alvo | Justificativa |
|---------|-----------------|-------------------|---------------|
| P1 — Emergente | 100–999 ★ | 40% | Projetos com menor visibilidade, possivelmente com práticas de acessibilidade menos maduras |
| P2 — Estabelecido | 1.000–9.999 ★ | 40% | Projetos com comunidade ativa, mas não amplamente conhecidos fora de seu nicho |
| P3 — Popular | ≥ 10.000 ★ | 20% | Projetos de alta visibilidade; limitados a 20% para evitar dominância de projetos que podem ter práticas de acessibilidade atípicamente boas |

O teto de 20% para projetos P3 implementa a recomendação de Heckman e Williams (2011) de evitar o **viés de popularidade** (*popularity bias*) que ocorre quando amostras são dominadas pelos projetos mais conhecidos.

### 7.5 Cobertura dos Princípios WCAG

Além da estratificação tridimensional, o corpus requer que a distribuição de violações confirmadas cubra os quatro princípios WCAG com densidades mínimas:

| Princípio | Requisito Mínimo |
|-----------|-----------------|
| Perceivable (1.x) | ≥ 20 projetos com pelo menos 1 violação confirmada |
| Operable (2.x) | ≥ 20 projetos com pelo menos 1 violação confirmada |
| Understandable (3.x) | ≥ 15 projetos com pelo menos 1 violação confirmada |
| Robust (4.x) | ≥ 20 projetos com pelo menos 1 violação confirmada |

Caso após a varredura inicial um princípio esteja sub-representado, realiza-se amostragem suplementar direcionada (*targeted supplementary sampling*) com consultas de busca específicas para o tipo de violação sub-representada.

---

## 8. Protocolo de Captura de Instantâneos (*Snapshotting*)

### 8.1 Motivação para Pinagem de *Commits*

O princípio fundamental do *snapshotting* é a **imutabilidade do objeto de estudo**: os projetos avaliados devem permanecer exatamente idênticos em todos os momentos em que são avaliados, mesmo que o repositório original continue evoluindo. Este requisito é idêntico ao adotado pelo Defects4J (Just et al., 2014) e é indispensável para a reprodutibilidade dos resultados.

Sem a pinagem de *commits*, um pesquisador que reproduzisse o estudo 6 meses mais tarde poderia estar avaliando uma versão completamente diferente do código, tornando inválida qualquer comparação com os resultados originais.

### 8.2 Procedimento de Captura

O script `dataset/scripts/snapshot.py` implementa o seguinte procedimento de cinco etapas:

**Etapa 1 — Identificação do branch canônico**: Consulta `GET /repos/{owner}/{repo}` via API GitHub para obter o campo `default_branch`, que representa o branch principal do projeto (comumente `main` ou `master`, mas variável por projeto).

**Etapa 2 — Recuperação do SHA-1 do *commit* HEAD**: Consulta `GET /repos/{owner}/{repo}/commits/{branch}` para obter o hash completo de 40 caracteres hexadecimais do *commit* mais recente no branch canônico no momento da coleta. Este hash é registrado no campo `pinned_commit` da `SnapshotMetadata`.

**Etapa 3 — Registro de metadados de snapshot**: Persiste no catálogo: `pinned_commit` (SHA-1 completo), `snapshot_date` (timestamp ISO 8601 UTC da coleta), `branch` (nome do branch).

**Etapa 4 — Clone superficial na revisão pinada**:

```bash
# Método primário (se a plataforma suporta clone por SHA)
git clone --depth 1 https://github.com/{owner}/{repo}.git snapshots/{id}/
git -C snapshots/{id}/ checkout {pinned_commit}

# Método alternativo (se o método primário falhar)
git clone https://github.com/{owner}/{repo}.git snapshots/{id}/
git -C snapshots/{id}/ checkout {pinned_commit}
```

O clone superficial (`--depth 1`) reduz significativamente o tempo e o espaço em disco necessários, pois não baixa o histórico completo do repositório — apenas o estado do código na revisão especificada.

**Etapa 5 — Verificação de integridade**: Executa `git -C snapshots/{id}/ rev-parse HEAD` e compara o resultado com `pinned_commit`. Um hash que não corresponde indica corrupção do clone ou manipulação não autorizada, e a entrada é marcada como `error` no catálogo.

### 8.3 Extração de Metadados de Ambiente

Após o clone, o script extrai e registra metadados do ambiente de desenvolvimento do projeto:

- `react_version`: valor de `dependencies.react` em `package.json` (versão exata ou range semver).
- `typescript_version`: valor de `devDependencies.typescript` em `package.json`.
- `component_file_count`: contagem de arquivos `.tsx`/`.jsx` nos `scan_paths` após exclusão dos `exclude_paths`.
- `clone_size_mb`: tamanho do diretório do clone em megabytes.

Estes metadados permitem análises de sub-grupos por versão de React ou TypeScript e fornecem insumos para a classificação de tamanho (critério IC4 / Dimensão S1–S3).

### 8.4 Armazenamento e Política de Não-Distribuição

Os clones são armazenados em `dataset/snapshots/<owner>__<repo>/` e estão listados no `.gitignore` do repositório do dataset. O corpus não redistribui código-fonte; em vez disso, o catálogo (`projects.yaml`) contém informações suficientes para que qualquer pesquisador recrie exatamente qualquer snapshot executando:

```bash
git clone https://github.com/{owner}/{repo}.git
git checkout {pinned_commit}
```

Esta política está em conformidade com os termos de serviço do GitHub (seção D.3, que permite acesso a repositórios públicos para fins de pesquisa) e com as licenças de código aberto dos projetos incluídos, que permitem análise mas não necessariamente redistribuição.

---

## 9. Protocolo de Varredura Multi-Ferramenta

### 9.1 Justificativa para a Abordagem Multi-Ferramenta

Estudos comparativos de ferramentas de avaliação de acessibilidade automatizada (Vigo et al., 2013; Brajnik, 2008; Abou-Zahra, 2008) demonstraram consistentemente que nenhuma ferramenta individual cobre um subconjunto suficientemente abrangente dos critérios WCAG detectáveis automaticamente. Vigo et al. (2013) encontraram que a taxa de concordância entre ferramentas populares para o mesmo conjunto de páginas é de apenas 36%, indicando complementaridade significativa entre ferramentas.

A abordagem de **consenso multi-ferramenta** adotada neste corpus serve a dois propósitos:

1. **Maximizar a cobertura de critérios detectáveis**: A união dos critérios cobertos por quatro ferramentas é substancialmente maior do que qualquer ferramenta individual.
2. **Reduzir falsos positivos**: Uma violação confirmada por duas ou mais ferramentas independentes tem probabilidade muito menor de ser um falso positivo do que uma violação detectada por apenas uma ferramenta.

### 9.2 Ferramentas Utilizadas

| Ferramenta | Versão | Mecanismo de Detecção | Critérios WCAG Primários |
|------------|--------|----------------------|--------------------------|
| **pa11y** | ≥ 6.2.3 | HTML estático + renderização Puppeteer | 1.1.1, 1.3.x, 1.4.3, 2.1.1, 4.1.x |
| **axe-core** | ≥ 4.9.1 | Injeção de script em página renderizada | 1.1.1, 1.3.x, 1.4.x, 2.x, 3.x, 4.1.x |
| **Lighthouse (a11y)** | ≥ 11.x | Análise de página renderizada completa | 1.4.3, 2.1.1, 2.4.x, 4.1.2 |
| **Playwright + axe** | ≥ 1.40 + axe 4.x | Automação de navegador + injeção axe | Conjunto completo axe-core |

*Nota*: A varredura em larga escala desabilita o Lighthouse (`use_lighthouse=False`) por razões de desempenho; o Lighthouse é utilizado apenas em análises pontuais de validação.

### 9.3 Processo de Deduplicação e Consenso

A multiplicidade de ferramentas exige um protocolo de deduplicação robusto para evitar que a mesma violação física seja contada múltiplas vezes. O protocolo de detecção do a11y-autofix (`protocol/detection.py`) implementa deduplicação baseada em **chave de conteúdo composta**:

```
chave_de_deduplicação = hash(seletor_css | critério_wcag)
```

Todos os resultados de ferramentas que geram a mesma chave são consolidados em um único objeto `A11yIssue`, com o campo `tool_consensus` registrando quantas ferramentas independentes o detectaram, e `found_by` listando os nomes dessas ferramentas.

O nível de confiança (*confidence*) é atribuído conforme o consenso:

| `tool_consensus` | `confidence` | Interpretação |
|-----------------|-------------|---------------|
| ≥ 2 | `high` | Violação corroborada por múltiplas ferramentas independentes; automaticamente aceita como ground truth |
| 1 | `medium` ou `low` | Detectada por apenas uma ferramenta; requer validação humana |

### 9.4 Configuração Fixa da Varredura para o Corpus

Para garantir a comparabilidade entre projetos, todos os projetos são varridos com a seguinte configuração fixa:

```yaml
wcag_level: WCAG2AA
min_tool_consensus: 1   # coleta TODOS os findings; consenso aplicado na análise
scan_timeout: 90        # segundos por arquivo por ferramenta
max_concurrent_scans: 2 # paralelismo interno por projeto
workers: 1              # projetos em paralelo (ajustável conforme hardware)
```

O `min_tool_consensus: 1` durante a coleta é intencional: coleta-se o conjunto máximo de candidatos para análise e anotação posterior. O limiar de consenso é aplicado na fase de análise, permitindo estudos com diferentes limiares sem re-varredura.

### 9.5 Persistência dos Resultados de Varredura

Para cada projeto varrido, o script `scan.py` produz três artefatos persistidos em `dataset/results/<project-id>/`:

1. **`scan_results.json`**: Trilha de auditoria completa contendo os objetos `ScanResult` serializados para cada arquivo varrido, incluindo os resultados brutos de cada ferramenta antes da deduplicação.

2. **`summary.json`**: `FindingSummary` agregado com contagens por tipo de violação, por princípio WCAG, por impacto, por critério específico, e estatísticas de duração e ferramentas utilizadas.

3. **`findings.jsonl`**: Um `ScanFinding` por linha em formato JSONL (*JSON Lines*), representando cada violação única após deduplicação. Este formato é escolhido por permitir processamento *streaming* de arquivos grandes sem carregamento completo em memória.

Adicionalmente, o script mantém um arquivo consolidado `dataset/results/dataset_findings.jsonl` com os findings de todos os projetos, e `dataset/results/dataset_stats.json` com estatísticas agregadas do corpus.

---

## 10. Protocolo de Anotação de Verdade Fundamental

### 10.1 Necessidade de Ground Truth

A avaliação de um sistema de detecção de acessibilidade requer uma estimativa da **taxa de falso positivo** (*false positive rate*) — a proporção de violações reportadas que, na verdade, não constituem violações segundo as WCAG. Sem essa estimativa, é impossível distinguir um sistema de alta precisão de um sistema que simplesmente reporta mais violações. A construção da verdade fundamental é o processo que torna possível esta estimativa.

Estudos anteriores que utilizaram apenas resultados de ferramentas automatizadas sem validação humana foram criticados por Brajnik (2008) exatamente por não distinguir entre violações genuínas e artefatos de ferramentas. O presente corpus adota a abordagem de dupla anotação com cômputo de concordância inter-anotadores, que constitui o padrão metodológico em estudos de construção de datasets de alta qualidade (Hripcsak e Rothschild, 2005).

### 10.2 Estratégia de Anotação Semi-Automatizada

A anotação segue uma estratégia em dois níveis que equaciona custo de anotação e qualidade:

#### 10.2.1 Nível 1: Aceitação Automática por Alta Confiança

Violações satisfazendo **ambas** as seguintes condições são automaticamente aceitas como *ground truth* confirmado sem revisão humana:

- `tool_consensus ≥ 2`: detectada por pelo menos dois detectores independentes.
- `confidence == "high"`: classificada como alta confiança pelo protocolo de detecção.

**Justificativa teórica**: A independência estatística de ferramentas de acessibilidade construídas com bases de código distintas (pa11y usa HTML Codesniffer; axe-core usa seu próprio motor de regras; Playwright+axe usa axe injetado em um contexto de navegador real) significa que a probabilidade de ambas reportarem um falso positivo para o mesmo elemento é o produto das probabilidades individuais de falso positivo. Se cada ferramenta tem, individualmente, uma taxa de FP de 15% (estimativa conservadora baseada em Vigo et al., 2013), a taxa de FP de um finding de consenso-2 é aproximadamente 15% × 15% = 2,25%.

**Implementação**: A função `auto_accept_findings()` em `annotate.py` itera sobre os `ScanFinding` e cria registros `GroundTruthFinding` com `auto_accepted=True` e `ground_truth_label=CONFIRMED` para findings elegíveis. O `model_validator` da classe `GroundTruthFinding` garante consistência desta lógica no nível do modelo de dados.

#### 10.2.2 Nível 2: Anotação Humana Dupla para Findings Disputados

Findings com `tool_consensus == 1` (detectados por apenas uma ferramenta) são submetidos a dois anotadores independentes que atribuem um de três rótulos:

- **`CONFIRMED`**: O anotador verificou manualmente que a violação descrita é genuína segundo as WCAG 2.1/2.2 no nível especificado.
- **`FALSE_POSITIVE`**: O anotador verificou que o elemento em questão é acessível apesar do alerta da ferramenta (ex.: a ferramenta não reconheceu um atributo ARIA proprietário válido).
- **`UNCERTAIN`**: O anotador não conseguiu determinar com confiança se a violação é genuína (ex.: o contexto completo de uso do componente não está disponível no código estático).

A interface de anotação (`annotate.py`) apresenta ao anotador: o ID do finding, o caminho do arquivo, o seletor CSS, o critério WCAG, o tipo de issue, o impacto reportado, as ferramentas que o detectaram, e a mensagem de diagnóstico da ferramenta. O anotador pode opcionalmente adicionar notas textuais livres para fundamentar sua decisão.

**Perfil de anotadores**: Dois anotadores com no mínimo 1 ano de experiência profissional em desenvolvimento web acessível e conhecimento demonstrado das WCAG 2.x (documentado por certificação CPACC, WAS, ou equivalente, ou por produção de código que passou em auditoria de conformidade).

### 10.3 Cômputo de Concordância Inter-Anotadores (Cohen's κ)

A qualidade da anotação humana é mensurada pelo **coeficiente κ de Cohen** (Cohen, 1960), que corrige a concordância observada pelo nível de concordância esperado por acaso:

$$\kappa = \frac{p_o - p_e}{1 - p_e}$$

onde:
- $p_o = \frac{\text{número de itens em que A1 e A2 concordaram}}{\text{total de itens anotados por ambos}}$ é a concordância observada.
- $p_e = \sum_{k \in K} P(A1=k) \cdot P(A2=k)$ é a concordância esperada por acaso, com $K = \{CONFIRMED, FALSE\_POSITIVE, UNCERTAIN\}$.

A implementação em `annotate.py::compute_cohens_kappa()` opera sobre listas de rótulos em formato de string, aplicando a fórmula acima para o caso multi-classe (três categorias):

```python
def compute_cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    n = len(labels_a)
    classes = sorted(set(labels_a) | set(labels_b))
    p_o = sum(a == b for a, b in zip(labels_a, labels_b)) / n
    p_e = sum((labels_a.count(c)/n) * (labels_b.count(c)/n) for c in classes)
    return round((p_o - p_e) / (1 - p_e), 4) if abs(1 - p_e) > 1e-10 else 1.0
```

A interpretação do κ segue a escala de Landis e Koch (1977):

| Intervalo de κ | Interpretação | Ação Requerida |
|---------------|---------------|----------------|
| κ ≥ 0.80 | Quase perfeito | Aceito sem ação adicional |
| 0.60 ≤ κ < 0.80 | Substancial | Aceito com revisão de casos discordantes |
| 0.40 ≤ κ < 0.60 | Moderado | Revisão conjunta e re-anotação dos discordantes |
| κ < 0.40 | Fraco | Re-treinamento dos anotadores; re-anotação integral |

**Limiar mínimo obrigatório**: κ ≥ 0.70 (métrica de qualidade QM6). Corpus com κ < 0.70 não é considerado adequado para publicação.

### 10.4 Reconciliação de Discordâncias

Para findings onde os dois anotadores discordaram (`agreement == False`):

1. Os dois anotadores realizam uma sessão de reconciliação estruturada, revisando conjuntamente o código e as diretrizes de anotação.
2. Se chegam a consenso, o rótulo consensual é registrado como `ground_truth_label` e `agreement` é atualizado para `True`.
3. Se persistem em discordância após a sessão de reconciliação, o finding recebe `ground_truth_label = UNCERTAIN` e é excluído do conjunto de avaliação primário (retido em um conjunto de análise de sensibilidade).

### 10.5 Consolidação do Ground Truth

O script `annotate.py --consolidate` produz `dataset/results/ground_truth_all.jsonl`, contendo todos os registros `GroundTruthFinding` com `ground_truth_label` definido (seja por aceitação automática ou por anotação humana concluída) de todos os projetos do corpus.

---

## 11. Métricas de Qualidade do Corpus

O script `validate.py` implementa 8 verificações de qualidade (QM1–QM8) que devem ser aprovadas antes de o corpus ser considerado adequado para uso em publicações acadêmicas. Cada verificação computa uma métrica observada e a compara com um limiar mínimo derivado da literatura.

### 11.1 QM1 — Tamanho do Corpus

**Definição**: Número de projetos com pelo menos 1 finding confirmado de ground truth.

**Limiar**: ≥ 20 projetos.

**Justificativa**: O limiar de 20 projetos representa o mínimo para que análises de sub-grupos por domínio (N ≥ 20, 7 domínios = ~3 por domínio em média) e análises estatísticas com poder razoável sejam possíveis. O tamanho-alvo do corpus é 50 projetos; o limiar de 20 é o mínimo absoluto para publicação parcial.

### 11.2 QM2 — Cobertura de Domínios

**Definição**: Número de domínios distintos representados por projetos com status `SCANNED` ou `ANNOTATED`.

**Limiar**: Todos os 7 domínios representados.

**Justificativa**: A representatividade entre domínios é condição necessária para afirmações de generalidade. Um corpus com lacunas de domínio não suporta conclusões sobre o comportamento do sistema em domínios ausentes.

### 11.3 QM3 — Cobertura de Princípios WCAG

**Definição**: Para cada um dos 4 princípios WCAG (Perceptível, Operável, Compreensível, Robusto), a contagem de findings confirmados mapeados para aquele princípio.

**Limiar**: Cada princípio com ≥ 10 findings confirmados.

**Justificativa**: Sistemas de remediação que são avaliados apenas sobre um subconjunto dos princípios WCAG podem apresentar comportamento radicalmente diferente nos princípios não cobertos. A cobertura mínima de 10 findings por princípio garante que cada dimensão de acessibilidade tem peso suficiente nas análises comparativas.

### 11.4 QM4 — Cobertura de Tipos de Issue

**Definição**: Número de tipos de issue (`IssueType`) distintos com pelo menos 1 finding confirmado.

**Limiar**: ≥ 5 tipos distintos.

**Justificativa**: A taxonomia de tipos de issue do a11y-autofix inclui 7 classes principais: `contrast`, `aria`, `keyboard`, `label`, `semantic`, `alt_text`, `focus`. Um corpus com menos de 5 tipos cobertos não oferece diversidade suficiente para comparar o comportamento de sistemas entre diferentes classes de violações.

### 11.5 QM5 — Taxa de Falso Positivo

**Definição**: $\text{FP rate} = \frac{\text{confirmed}\ FALSE\_POSITIVE}{\text{confirmed}\ (CONFIRMED + FALSE\_POSITIVE)}$

**Limiar**: ≤ 30%.

**Justificativa**: Taxas de FP acima de 30% indicam que as ferramentas de detecção estão reportando uma proporção excessiva de alertas não-genuínos, comprometendo a validade das comparações. Este limiar é derivado da literatura: Vigo et al. (2013) encontraram taxas de FP entre 15–40% em ferramentas individuais; o limiar de 30% é conservador para o conjunto de findings de consensus-1.

### 11.6 QM6 — Concordância Inter-Anotadores (Cohen's κ)

**Definição**: κ médio calculado sobre todos os projetos com anotação dupla completa.

**Limiar**: κ ≥ 0.70 (nível "substancial" segundo Landis e Koch, 1977).

**Justificativa**: κ < 0.70 indica que os anotadores têm interpretações significativamente diferentes das diretrizes de anotação, comprometendo a confiabilidade do ground truth. Este limiar é amplamente adotado em trabalhos de construção de datasets de NLP e de SE (Alshayban et al., 2020; Hripcsak e Rothschild, 2005).

### 11.7 QM7 — Integridade dos Snapshots

**Definição**: Proporção de projetos com status SNAPSHOTTED/SCANNED/ANNOTATED cujo SHA-1 verificado (`git rev-parse HEAD`) corresponde ao `pinned_commit` registrado no catálogo.

**Limiar**: 100%.

**Justificativa**: Qualquer discrepância indica que o snapshot foi modificado após a captura (corrompendo a reprodutibilidade) ou que o processo de captura não foi executado corretamente. A integridade dos snapshots é uma propriedade binária: ou é garantida para todos os projetos, ou não é garantida.

### 11.8 QM8 — Proporção de Findings de Alta Confiança

**Definição**: $\text{high-conf ratio} = \frac{\text{confirmados com confidence=high}}{\text{total de confirmados}}$

**Limiar**: ≥ 40%.

**Justificativa**: Uma proporção muito baixa de findings de alta confiança indica que o corpus é dominado por findings de consenso-1, que são mais propensos a serem falsos positivos e mais difíceis de alinhar com ground truth preciso. O limiar de 40% garante que uma fração substancial do corpus tem corroboração multi-ferramenta.

---

## 12. Análises Estatísticas

O script `analyze.py` implementa 9 análises estatísticas (A1–A9) destinadas a caracterizar o corpus e a responder às questões de pesquisa.

### 12.1 A1 — Estatísticas do Corpus

Computa estatísticas descritivas básicas: total de projetos, arquivos varridos, findings confirmados, médias de findings por projeto e por arquivo. Estes dados compõem a tabela de descrição do dataset que tipicamente abre a seção de avaliação em artigos que utilizam o corpus.

### 12.2 A2 — Princípio WCAG × Impacto

Tabela de contingência cruzando os 4 princípios WCAG com os 4 níveis de impacto (`critical`, `serious`, `moderate`, `minor`). Permite identificar, por exemplo, se violações de princípio Operável tendem a ser mais severas do que violações de princípio Compreensível.

### 12.3 A3 — Ranking Pareto de Tipos de Issue

Lista os tipos de issue em ordem decrescente de frequência, com porcentagem acumulada. O *Princípio de Pareto* (Juran, 1954) — que, na maioria dos sistemas, ~80% dos problemas são causados por ~20% das causas — frequentemente aplica-se a violações de acessibilidade: estudos anteriores (Bajammal e Mesbah, 2021) encontraram que contraste de cor e rótulos faltantes respondem por mais de 60% de todas as violações detectáveis.

### 12.4 A4 — Distribuição por Domínio

Para cada domínio: número de projetos, arquivos varridos, findings, taxa de findings por 100 arquivos, e número de falsos positivos. Esta análise permite testar a hipótese de que perfis de violação variam sistematicamente entre domínios de aplicação.

### 12.5 A5 — Consenso Multi-Ferramenta

Distribuição da variável `tool_consensus` (1, 2, 3, 4) nos findings confirmados. Análise das proporções de findings de ferramenta única vs. multi-ferramenta e sua relação com a taxa de FP estimada.

### 12.6 A6 — Complexidade × Confiança

Tabela de contingência entre complexidade de reparo (`trivial`, `simple`, `moderate`, `complex`) e nível de confiança. Permite verificar se violações mais complexas tendem a ser detectadas com menor confiança (o que seria esperado, dado que violações complexas frequentemente requerem contexto semântico que ferramentas automatizadas não capturam).

### 12.7 A7 — Taxa de Falso Positivo por Domínio

Estima a taxa de FP separadamente para cada domínio de aplicação usando os projetos anotados. Testa se a taxa de FP varia sistematicamente entre domínios — o que impactaria a interpretação de resultados de detecção específicos de domínio.

### 12.8 A8 — Densidade de Findings

Calcula o número de findings por 100 arquivos de componentes para cada projeto, com distribuição (mínimo, máximo, média, mediana). A densidade de findings é uma métrica que normaliza o número absoluto de violações pelo tamanho do projeto, permitindo comparações entre projetos de diferentes tamanhos.

### 12.9 A9 — Top-20 Critérios WCAG mais Frequentes

Lista os critérios WCAG específicos (ex.: 1.4.3 — Contrast, 4.1.2 — Name Role Value) mais frequentemente violados. Esta análise tem valor prático imediato: informa quais critérios devem ser priorizados por sistemas de remediação automática para maximizar o impacto.

### 12.10 Exportação LaTeX

O método `export_latex()` em `analyze.py` produz tabelas formatadas para inclusão direta em manuscritos LaTeX usando os pacotes `booktabs` e `tabular`. As tabelas exportadas incluem: distribuição de domínios e distribuição dos top-10 tipos de issue.

---

## 13. Modelos de Dados e Representação Formal

### 13.1 Filosofia de Design

Todos os artefatos do dataset são representados como modelos Pydantic v2 (Colvin, 2023), que oferecem: (i) validação de esquema em tempo de construção; (ii) serialização/deserialização JSON e YAML bidirecional; (iii) documentação de campos através de `Field(description=...)`. Esta abordagem garante que o catálogo permaneça internamente consistente mesmo ao ser manipulado por múltiplos scripts.

### 13.2 Hierarquia de Modelos

```
DatasetMetadata                   ← metadados do dataset completo
  └─ DatasetSplit                 ← divisão train/val/test
  └─ AnnotationAgreement          ← κ agregado do corpus
ProjectEntry                      ← entrada central do catálogo
  ├─ GitHubMetadata               ← metadados do repositório GitHub
  ├─ SnapshotMetadata             ← pinned_commit + versões
  ├─ ScreeningRecord              ← resultados IC1–IC7, EC1–EC7
  └─ ProjectScanSummary           ← resumo da varredura
       └─ FindingSummary          ← contagens agregadas por tipo/princípio/impacto
ScanFinding                       ← violação individual (pós-deduplicação)
GroundTruthFinding                ← ScanFinding + rótulo de anotação
AnnotationAgreement               ← κ por projeto
```

### 13.3 Invariantes de Integridade

Os seguintes invariantes são garantidos pelos validadores Pydantic:

- `ProjectEntry.id == "{owner}__{repo}"` (validador `id_must_match_owner_repo`).
- `ProjectEntry.github_url` contém `"{owner}/{repo}"` (validador `url_must_contain_owner_repo`).
- `SnapshotMetadata.pinned_commit` é vazio ou um hash SHA-1 de 40 caracteres hexadecimais (regex `^([0-9a-f]{40})?$`).
- `GroundTruthFinding` com `tool_consensus ≥ 2` e sem `annotator_1_label` é automaticamente marcado com `auto_accepted=True` e `ground_truth_label=CONFIRMED` (validador `set_auto_label`).

---

## 14. Reprodutibilidade e Pacote de Replicação

### 14.1 Princípios de Reprodutibilidade

A construção do corpus adota os princípios de reprodutibilidade computacional formulados por Claerbout e Karrenbach (1992) e formalizados para estudos de engenharia de software por Gonzalez-Barahona e Robles (2012): (i) disponibilidade dos dados brutos; (ii) disponibilidade dos scripts de análise; (iii) documentação completa do ambiente de execução; (iv) determinismo dos procedimentos de amostragem e análise.

### 14.2 Componentes do Pacote de Replicação

| Artefato | Localização | Conteúdo |
|----------|-------------|----------|
| Catálogo de projetos | `dataset/catalog/projects.yaml` | Todos os metadados de projetos, com commits pinados |
| Protocolo de construção | `dataset/PROTOCOL.md` | Decisões metodológicas formalizadas |
| Este documento | `dataset/README_DATASET_COMPLETE.md` | Justificativas detalhadas de todas as decisões |
| Script de descoberta | `dataset/scripts/discover.py` | Reproduz a coleta do catálogo |
| Script de snapshot | `dataset/scripts/snapshot.py` | Reproduz clones e pinagem de commits |
| Script de varredura | `dataset/scripts/scan.py` | Reproduz a varredura de acessibilidade |
| Script de anotação | `dataset/scripts/annotate.py` | Documenta o processo de anotação e κ |
| Script de validação | `dataset/scripts/validate.py` | Verifica os 8 critérios de qualidade |
| Script de análise | `dataset/scripts/analyze.py` | Reproduz todas as análises estatísticas |
| Modelos de dados | `dataset/schema/models.py` | Esquema formal de todos os artefatos |
| Resultados de varredura | `dataset/results/` | Findings brutos e de ground truth por projeto |
| Versões de ferramentas | `dataset/results/dataset_stats.json` | Versões exatas de todas as ferramentas |

### 14.3 Pipeline de Reprodução Completa

Para reproduzir todos os artefatos do corpus do zero:

```bash
# 1. Reconstruir catálogo por descoberta
python dataset/scripts/discover.py --token $GITHUB_TOKEN

# 2. Recriar snapshots (clones com commits pinados)
python dataset/scripts/snapshot.py --catalog dataset/catalog/projects.yaml

# 3. Re-executar varredura multi-ferramenta
python dataset/scripts/scan.py --catalog dataset/catalog/projects.yaml

# 4. Re-aplicar aceitação automática
python dataset/scripts/annotate.py --auto-accept-only

# 5. Validar qualidade do corpus
python dataset/scripts/validate.py --strict

# 6. Gerar relatório de análise
python dataset/scripts/analyze.py --output-dir reports/ --latex
```

---

## 15. Considerações Éticas, Legais e de Privacidade

### 15.1 Conformidade com Licenças de Código Aberto

O critério IC3 garante que todos os projetos incluídos estão sob licenças aprovadas pela OSI. O dataset não redistribui código-fonte; registra apenas metadados estruturados (URLs de repositório, hashes de commits, resultados de análise estática). A reprodução requer que o pesquisador clone os repositórios originais, o que está em conformidade com os termos de todas as licenças incluídas.

### 15.2 Conformidade com os Termos de Serviço do GitHub

O acesso à API GitHub para fins de pesquisa é permitido pela Seção D.3 dos Termos de Serviço do GitHub (versão 2024). As seguintes restrições são rigorosamente observadas: (i) todas as requisições são autenticadas com token pessoal de acesso; (ii) a taxa de requisições não excede os limites documentados (30 req/min para Search API); (iii) nenhum dado de usuário (nomes de usuário, emails de autores de commits) é coletado ou processado.

### 15.3 Privacidade de Dados

Os scripts de coleta não acessam, armazenam ou processam nenhum dado pessoal de usuários do GitHub. Os commits pinados são identificados exclusivamente pelo hash SHA-1, não pelos metadados do autor. Informações de autores (nome, email) presentes no histórico de commits não são extraídas nem armazenadas.

### 15.4 Considerações sobre Conteúdo dos Projetos

Os projetos incluídos podem conter conteúdo sensível no código (ex.: chaves de API parcialmente visíveis em arquivos de exemplo). A varredura de acessibilidade opera exclusivamente sobre a estrutura sintática do JSX/HTML e não processa nem reporta conteúdo de dados dinâmico. Qualquer segredo eventualmente presente no código-fonte permanece no clone local e não é transmitido para APIs externas.

---

## 16. Ameaças à Validade

### 16.1 Ameaças à Validade de Construto

**Cobertura parcial das WCAG**: Ferramentas automatizadas estimam cobrir entre 30–40% dos critérios de sucesso WCAG detectáveis automaticamente (Vigo et al., 2013). Os ~60–70% restantes requerem julgamento humano sobre contexto semântico, adequação cognitiva e outros aspectos subjetivos. **Mitigação**: Uso de quatro ferramentas complementares para maximizar a cobertura; documentação dos critérios não cobertos.

**Qualidade da verdade fundamental**: A aceitação automática de findings com consensus ≥ 2 não é equivalente à verificação humana exaustiva de cada violação. **Mitigação**: Verificação humana de amostra aleatória estratificada de findings auto-aceitos (taxa de amostragem ≥ 5%); reportagem da taxa de discrepância na amostra verificada.

### 16.2 Ameaças à Validade Interna

**Viés de seleção no frame de amostragem**: A GitHub Search API não oferece acesso exaustivo a todos os repositórios; resultados são influenciados por algoritmos de relevância internos não documentados. **Mitigação**: Múltiplas consultas por domínio com variações de termos; análise de sensibilidade comparando diferentes estratégias de busca.

**Contaminação do modelo**: LLMs de grande porte podem ter sido pré-treinados em código dos projetos do corpus, inflacionando artificialmente as métricas de reparo para esses projetos específicos. **Mitigação**: Registro das datas de corte de treinamento dos modelos avaliados; análise de sub-grupos separando projetos que provavelmente estão no corpus de treinamento (alta popularidade, com muito conteúdo indexado) dos demais.

### 16.3 Ameaças à Validade Externa

**Viés de framework**: O corpus é exclusivamente React/TypeScript. Resultados podem não generalizar para Angular, Vue, Svelte ou Web Components nativos. **Mitigação**: Declaração explícita do escopo de generalização; replicação parcial em um subconjunto de Vue/Angular como análise de sensibilidade (trabalho futuro).

**Viés de código aberto**: Projetos de código aberto no GitHub não são representativos de todos os projetos React em produção, incluindo projetos corporativos privados que podem ter perfis de violação distintos. **Mitigação**: Documentação desta limitação; estudos de caso com colaboradores industriais como trabalho futuro.

### 16.4 Ameaças à Validade de Conclusão

**Variabilidade entre execuções de agentes LLM**: Modelos de linguagem produzem saídas estocásticas; a mesma violação pode ser reparada em algumas execuções e não em outras. **Mitigação**: Mínimo de 3 execuções por condição experimental; reporte de médias e desvios padrão; uso de temperatura fixa e seed documentado.

**Múltiplas comparações**: A avaliação simultânea de múltiplos modelos, múltiplos tipos de violação e múltiplos domínios aumenta a probabilidade de erro de Tipo I. **Mitigação**: Correção de Bonferroni para comparações múltiplas; reportagem de tamanhos de efeito (*d* de Cohen) além de valores p.

---

## 17. Referências Bibliográficas

Abou-Zahra, S. (2008). *Web accessibility evaluation*. W3C/WAI Tutorial Series.

Alshayban, A., Ahmed, I., & Malek, S. (2020). Accessibility issues in Android apps: State of affairs, user feedback, and ways forward. In *Proceedings of the 42nd IEEE/ACM International Conference on Software Engineering (ICSE 2020)* (pp. 1323–1334). IEEE.

Bajammal, M., & Mesbah, A. (2021). Semantic web accessibility testing via hierarchical visual analysis. In *Proceedings of the 18th International Web for All Conference (W4A 2021)*. ACM.

Brajnik, G. (2008). A comparative test of web accessibility evaluation methods. In *Proceedings of the 10th International ACM SIGACCESS Conference on Computers and Accessibility (ASSETS 2008)* (pp. 113–120). ACM.

Böhme, M., Pham, V. T., Nguyen, M. D., & Roychoudhury, A. (2017). Directed greybox fuzzing. In *Proceedings of the 2017 ACM SIGSAC Conference on Computer and Communications Security (CCS 2017)* (pp. 2329–2344). ACM.

Claerbout, J. F., & Karrenbach, M. (1992). Electronic documents give reproducible research a new meaning. In *SEG Technical Program Expanded Abstracts* (pp. 601–604). Society of Exploration Geophysicists.

Cochran, W. G. (1977). *Sampling techniques* (3rd ed.). John Wiley & Sons.

Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educational and Psychological Measurement*, 20(1), 37–46. https://doi.org/10.1177/001316446002000104

Cohen, J. (1988). *Statistical power analysis for the behavioral sciences* (2nd ed.). Lawrence Erlbaum Associates.

Colvin, S. (2023). *Pydantic v2 documentation*. Pydantic Services Inc. https://docs.pydantic.dev/

ETSI. (2021). *EN 301 549 V3.2.1: Accessibility requirements for ICT products and services*. European Telecommunications Standards Institute.

Gonzalez-Barahona, J. M., & Robles, G. (2012). On the reproducibility of empirical software engineering studies based on data retrieved from development repositories. *Empirical Software Engineering*, 17(1–2), 75–89.

Heckman, S., & Williams, L. (2011). A systematic literature review of actionable alert identification techniques for automated static code analysis. *Information and Software Technology*, 53(4), 363–387.

Hripcsak, G., & Rothschild, A. S. (2005). Agreement, the F-measure, and reliability in information retrieval. *Journal of the American Medical Informatics Association*, 12(3), 296–298.

Hutchins, M., Foster, H., Goradia, T., & Ostrand, T. (1994). Experiments on the effectiveness of dataflow- and controlflow-based test adequacy criteria. In *Proceedings of the 16th IEEE International Conference on Software Engineering (ICSE 1994)* (pp. 191–200). IEEE.

Just, R., Jalali, D., & Ernst, M. D. (2014). Defects4J: A database of existing faults to enable controlled testing studies for Java programs. In *Proceedings of the 2014 International Symposium on Software Testing and Analysis (ISSTA 2014)* (pp. 437–440). ACM.

Juran, J. M. (1954). Universals in management planning and controlling. *The Management Review*, 43(11), 748–761.

Kalliamvakou, E., Gousios, G., Blincoe, K., Singer, L., German, D. M., & Damian, D. (2014). The promises and perils of mining GitHub. In *Proceedings of the 11th Working Conference on Mining Software Repositories (MSR 2014)* (pp. 92–101). ACM.

Kitchenham, B. (2004). *Procedures for performing systematic reviews*. Keele University Technical Report TR/SE-0401.

Kleppmann, M. (2017). *Designing data-intensive applications*. O'Reilly Media.

Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*, 33(1), 159–174.

Lazar, J., Dudley-Sponaugle, A., & Greenidge, K. D. (2004). Improving web accessibility: A study of webmaster perceptions. *Computers in Human Behavior*, 20(2), 269–288.

Le Goues, C., Holtschulte, N., Smith, E. K., Brun, Y., Devanbu, P., Forrest, S., & Weimer, W. (2015). The ManyBugs and IntroClass benchmarks for automated repair of C programs. *IEEE Transactions on Software Engineering*, 41(12), 1236–1256.

Mattsson, M., et al. (2021). Codebase characteristics of React applications on npm (informal estimates from ecosystem analysis). Cited as approximation.

Power, C., Freire, A., Petrie, H., & Swallow, D. (2012). Guidelines are only half of the story: Accessibility problems encountered by blind users on the web. In *Proceedings of CHI 2012* (pp. 433–442). ACM.

União Europeia. (2016). *Directiva (EU) 2016/2102 do Parlamento Europeu e do Conselho, de 26 de outubro de 2016, relativa à acessibilidade dos sítios web e das aplicações móveis dos organismos do setor público*. Jornal Oficial da União Europeia.

Vigo, M., Brown, J., & Conway, V. (2013). Benchmarking web accessibility evaluation tools: Measuring the harm of sole reliance on automated tests. In *Proceedings of the 10th International Cross-Disciplinary Conference on Web Accessibility (W4A 2013)*. ACM.

W3C. (2018). *Web Content Accessibility Guidelines (WCAG) 2.1*. W3C Recommendation. https://www.w3.org/TR/WCAG21/

W3C. (2023). *Web Content Accessibility Guidelines (WCAG) 2.2*. W3C Recommendation. https://www.w3.org/TR/WCAG22/

W3C/WAI. (2014). *Website Accessibility Conformance Evaluation Methodology (WCAG-EM) 1.0*. W3C Working Group Note. https://www.w3.org/TR/WCAG-EM/

WHO. (2023). *Disability and health: Key facts*. World Health Organization. https://www.who.int/news-room/fact-sheets/detail/disability-and-health

Wohlin, C., Runeson, P., Höst, M., Ohlsson, M. C., Regnell, B., & Wesslén, A. (2012). *Experimentation in software engineering*. Springer. https://doi.org/10.1007/978-3-642-29044-2

---

## Apêndice A — Ontologia de Tipos de Issue

| `IssueType` | Critérios WCAG Primários | Descrição |
|-------------|--------------------------|-----------|
| `contrast` | 1.4.3, 1.4.6, 1.4.11 | Taxa de contraste insuficiente entre texto/fundo ou componentes não-texto |
| `aria` | 4.1.2, 1.3.1 | Uso incorreto, desnecessário ou ausente de atributos ARIA |
| `keyboard` | 2.1.1, 2.1.2, 2.4.3 | Componentes inacessíveis por teclado; ordem de foco imprevisível |
| `label` | 1.3.1, 2.4.6, 3.3.2 | Rótulos ausentes ou insuficientemente descritivos em elementos de formulário |
| `semantic` | 1.3.1, 1.3.2, 2.4.6 | Uso incorreto de elementos semânticos HTML; estrutura de cabeçalhos incorreta |
| `alt_text` | 1.1.1 | Texto alternativo ausente, vazio ou não-descritivo em imagens informativas |
| `focus` | 2.4.7, 2.4.11 | Indicador de foco do teclado ausente ou com contraste insuficiente |

## Apêndice B — Mapeamento WCAG → IssueType

| Critério WCAG | Título | IssueType | Complexidade |
|---------------|--------|-----------|--------------|
| 1.1.1 | Non-text Content | `alt_text` | simple |
| 1.3.1 | Info and Relationships | `semantic` | moderate |
| 1.3.2 | Meaningful Sequence | `semantic` | complex |
| 1.4.3 | Contrast (Minimum) | `contrast` | simple |
| 1.4.6 | Contrast (Enhanced) | `contrast` | simple |
| 1.4.11 | Non-text Contrast | `contrast` | simple |
| 2.1.1 | Keyboard | `keyboard` | moderate |
| 2.1.2 | No Keyboard Trap | `keyboard` | complex |
| 2.4.3 | Focus Order | `focus` | moderate |
| 2.4.7 | Focus Visible | `focus` | simple |
| 2.4.11 | Focus Appearance | `focus` | moderate |
| 3.3.2 | Labels or Instructions | `label` | simple |
| 4.1.2 | Name, Role, Value | `aria` | moderate |

## Apêndice C — Fluxo Completo do Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PIPELINE DE CONSTRUÇÃO DO CORPUS                      │
└─────────────────────────────────────────────────────────────────────────┘

 GitHub API                discover.py                projects.yaml
 ─────────────────────────────────────────────────────────────────────────
 Repositórios ──[queries]──► Triagem automática  ──► Catálogo seed
 TypeScript/React           (IC1–IC5, EC1–EC4)        (status: PENDING)
                            ScreeningRecord

                              snapshot.py
 ─────────────────────────────────────────────────────────────────────────
 Catálogo  ──[git clone]──► Triagem manual      ──► snapshots/<id>/
 PENDING                    (IC6, IC7, EC5–EC7)      (status: SNAPSHOTTED)
                            SnapshotMetadata

                                scan.py
 ─────────────────────────────────────────────────────────────────────────
 snapshots/  ──[pa11y]──►  DetectionProtocol  ──► results/<id>/
             ──[axe]──►    deduplicação            findings.jsonl
             ──[playwright]►consenso              summary.json
                           ScanFinding             (status: SCANNED)

                              annotate.py
 ─────────────────────────────────────────────────────────────────────────
 findings.jsonl ──[consensus≥2]──► auto-accept  ──► ground_truth.jsonl
                ──[consensus=1]──► revisão humana    (status: ANNOTATED)
                                   Cohen's κ          ground_truth_all.jsonl

                              validate.py
 ─────────────────────────────────────────────────────────────────────────
 Corpus completo ──[QM1–QM8]──► Relatório de qualidade
                                 (pass/fail por métrica)

                              analyze.py
 ─────────────────────────────────────────────────────────────────────────
 ground_truth_all.jsonl ──[A1–A9]──► analysis_report.json
                                      tables/*.tex (LaTeX)
```
