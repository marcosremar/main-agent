# Roadmap Mestre — Plataforma de Aprendizagem de Línguas

Cidade 3D + Voz + LLM + NPCs + Classroom + Currículo CEFR + Avaliação Automática +
QA Visual + QA Pedagógico + Pesquisa Experimental — ponta a ponta.

`[ ]` todo · `[~]` in progress · `[x]` done (date it). Verify per pillar 4; engine/runtime
→ verde em browser + macOS + iPhone. This is the doctorate's master epic.

**Meta final**: o jogador vive um dia completo na cidade usando apenas a voz, recebendo
feedback pedagógico automático em cada interação, tudo validado por QA visual/pedagógico/funcional.

---

## ÉPICO 0 — Render & Validação Base (1–13)

### Cor desbotada no iPhone (Babylon Native / Metal)
- [ ] 0a. Render em espaço linear lava as cores no Babylon Native. RN funciona pois usa backbuffer sRGB.
- [ ] 0b. Correção promissora: backbuffer sRGB nativo (espelhar comportamento RN). Color grading NÃO resolveu.
- [ ] 0c. Captura de tela: usar `BC_GAME.screenshotPNG()` (Babylon), não screenshot do DOM (framebuffer vazio).

### 13 cenários de validação automática (Casa → Padaria → atividade oral)
- [ ] 1. Carregar a cidade
- [ ] 2. Entrar no modo Play
- [ ] 3. Iara cumprimenta o usuário
- [ ] 4. Caminhar pela cidade
- [ ] 5. Chegar à padaria
- [ ] 6. Entrar no prédio
- [ ] 7. Conversar com NPC
- [ ] 8. Iniciar atividade oral
- [ ] 9. Responder atividade
- [ ] 10. Avançar tarefa
- [ ] 11. Testar fallback
- [ ] 12. Concluir quest
- [ ] 13. Gerar relatório pedagógico

> Bloqueio atual: comando de voz/texto ("vamos") não aciona walk; NLU depende do LLM/Gateway.
> Sem LLM conectado, cenários 4–13 não avançam. Próximo passo = conectar LLM ao jogo.

---

## ÉPICO 1 — LLM no Gameplay & Cidade do Cotidiano (14–28)

- [ ] 14. Integração completa do LLM no gameplay — NPCs no gateway, comandos livres de voz, fala→ação, avaliar respostas
- [ ] 15. Sistema de atividades do cotidiano — padaria, restaurante, hotel, transporte, farmácia, banco
- [ ] 16. Avaliação automática da oralidade — pronúncia, fluência, gramática, vocabulário, pragmática → nota+feedback+próxima
- [ ] 17. Sistema de progressão pedagógica — A1→C1; cidade muda conforme nível
- [ ] 18. Testes automáticos Playwright por atividade — entrar, conversar, completar, recompensa, screenshots, vídeo
- [ ] 19. Validação por visão computacional — LLM multimodal valida screenshots/vídeos/HUD/diálogos/NPCs/objetivos/estado
- [ ] 20. Teste de voz real no iPhone — mic→VAD→ASR→LLM→ação→feedback; medir latência/erro/memória/bateria
- [ ] 21. **CRÍTICO**: resolver conflito ONNX ↔ Babylon Native — isolamento de runtime / VAD-ASR CoreML / init lazy (iPhone 12)
- [ ] 22. Cenário completo "Casa → Padaria" — fluxo validado: casa→Iara→objetivo→andar→localizar→porta→animação→entrar→interior→padeiro→atividade oral→compra→feedback
- [ ] 23. Auditoria de todos os cenários jogáveis — Casa→{Padaria,Farmácia,Restaurante,Transporte,Hotel,Banco,Praia,Mercado}: existe? funciona? NPC? atividade? avaliação? recompensa?
- [ ] 24. Grafo de progressão da cidade — doc automático: nós, NPCs, missões, objetivos pedagógicos, vocabulário, CEFR
- [ ] 25. Teste automático de todos os fluxos (Playwright) — fluxo padaria, fluxo farmácia, etc.
- [ ] 26. Detector de partes desconectadas — NPC sem missão, missão sem NPC, portal sem destino, loja sem atividade, atividade sem avaliação, avaliação sem feedback
- [ ] 27. Validação pedagógica da cidade inteira — cada local ensina o que deveria (padaria=comprar comida, farmácia=sintomas…)
- [ ] 28. Cenário "Dia Completo" — acordar→padaria→café→transporte→banco→almoçar→remédio→voltar. Teste final do sistema.

---

## ÉPICO 1.5 — CI / Branches / Arquitetura (38, 111–116)

- [ ] 38. Auditoria de branches e PRs — antes de cada merge: commits paralelos, classroom, rio-mobile, cidade; relatório de impacto; nada experimental no main
- [ ] PR#5 (feat/city-pedestrian-population → main): revisar ou mergear (contém correções de classroom + commits paralelos)
- [ ] 111. Monitoramento automático de PRs — CI→Playwright→LLM valida screenshots→relatório; nenhum PR sem validação
- [ ] 112. Resolução automática de conflitos — detectar arquivo dividido/main alterado/conflito previsto antes do merge
- [ ] 113. Dashboard de arquivos monolíticos (god files) — buildings.ts, game-runtime.ts, map-editor-state.ts, validate-plan.ts: tamanho, deps, complexidade
- [ ] 114. Decomposição contínua — arquivo > 1000 linhas → tarefa automática refatorar/dividir/testar
- [ ] 115. Compatibilidade pós-merge — main→typecheck→browser→admin→cidade→classroom automático
- [ ] 116. Inventário arquitetural — mapa automático core→runtime→gameplay→classroom→voice→mobile com deps explícitas

---

## ÉPICO Infra — Finetuning & GPU (49–60)

- [ ] 49. Pipeline de finetuning confiável — dataset→prep→treino→checkpoint→validação→push automático
- [ ] 50. Reuso de instâncias GPU — warm instance→reuse→treino→checkpoint→novo treino
- [ ] 51. Redução de imagens Docker — gpu-dev 8.63GB → meta <4GB; slim, camadas reutilizáveis, cache, separar treino/dev
- [ ] 52. Dashboard de deploy GPU — provider, GPU, status, boot, treino, custo
- [ ] 53. Diagnóstico de boot — pull? SSH? porta? container? health check? → relatório automático
- [ ] 54. Benchmark de providers — Vast vs RunPod vs outros: boot, preço, confiabilidade, disponibilidade
- [ ] 55. Validação automática do finetune — modelo→benchmarks→conversação→PT→roleplay→relatório
- [ ] 56. Benchmark de roleplay (línguas) — naturalidade, gramática, pragmática, manter papéis (padaria/restaurante/farmácia/hotel/banco)
- [ ] 57. Benchmark de latência mobile — ASR→LLM→TTS→resposta no iPhone 12/13/15
- [ ] 58. Recuperação automática — falha→checkpoint→nova GPU→retomar, sem humano
- [ ] 59. Infraestrutura totalmente automatizada — usuário inicia→escolhe GPU→treina→valida→publica→relatório
- [ ] 60. Integração com a tese — cada modelo validado para conversação PT, atividades pedagógicas, NPCs, feedback, relatórios

---

## ÉPICO QA Visual & Cidade (61–74)

- [ ] 61. Modo Turbo QA — cidade em velocidade 10x→NPCs→atividades→screenshots→LLM
- [ ] 62. Fast travel para testes — Casa→{Padaria,Farmácia,Banco,Restaurante} instantâneo (QA only)
- [ ] 63. Loop automático de validação — iterar captura→LLM avalia→correção até nota mínima
- [ ] 64. Score visual — cada cenário 0–100: identificação, posicionamento, NPCs, objetivos, clareza
- [ ] 65. Detector de objetos invisíveis — renderizou? atrás da parede? congelado? fora do frustum?
- [ ] 66. Verificação de POIs — cada POI: marcador, fachada, texto, NPC
- [ ] 67. Auditoria DynamicTexture — funciona Web? Native? precisa PNG/Sprite?
- [ ] 68. Biblioteca de placas nativas — PADARIA/FARMÁCIA/BANCO/HOTEL/RESTAURANTE pré-renderizados PNG
- [ ] 69. Navegação semântica — "ir para padaria" encontra caminho/fachada/atividade
- [ ] 70. QA por missão — objetivo→caminho→chegada→NPC→atividade→feedback validado individualmente
- [ ] 71. Replay automático — gravar posição/rotação/eventos/objetivos/falas; reproduzir teste exato
- [ ] 72. Benchmark de cenários — tabela cenário/tempo/score
- [ ] 73. Certificação de cenários — pronto só com score > 90 em visual/pedagógico/funcional
- [ ] 74. Cidade certificada — todas missões→loop automático→validação LLM→score>90→aprovado

---

## ÉPICO Classroom (75–100)

- [ ] 75. Concorrência real de sala — 10/50/100 alunos simultâneos: perda, corrupção, tempo, estabilidade
- [ ] 76. Teste de carga — professor publica→100 alunos entram/respondem→100 relatórios, automático
- [ ] 77. Persistência escalável — JSON→SQLite→Postgres→Cloud (turmas grandes)
- [ ] 78. Auditoria de race conditions — submissões/downloads/exportações/relatórios simultâneos
- [ ] 79. Dashboard do professor — progresso, notas, tentativas, relatórios, CSV, ranking opcional
- [ ] 80. Classroom analytics — conclusão, aprovação, tempo médio, erros frequentes, vocabulário problemático
- [ ] 81. Replay do aluno — aluno→tentativa→respostas→feedback
- [ ] 82. Classroom + Cidade — professor cria→aluno entra na cidade→missão→nota→professor acompanha
- [ ] 83. Classroom mobile (iPhone) — entrar com código, participar, enviar tentativa, receber feedback no app
- [ ] 84. Classroom + voz — mic→ASR→atividade oral→avaliação→relatório, sem texto manual
- [ ] 85. Segurança Classroom — auth, autorização, tokens de sessão, anti-spoofing (endpoints abertos hoje)
- [ ] 86. Certificação Classroom — concorrência, relatórios, professor, aluno, mobile, voz, escalabilidade
- [ ] 87. Integração final Classroom — ciclo completo professor→cidade→voz→NPC→LLM→registro→acompanha
- [ ] 88. Stress test 500 alunos — perda, corrupção, deadlocks, throughput
- [ ] 89. Migração para banco de dados — mutex não escala; JSON→SQLite→Postgres→Cloud
- [ ] 90. Recuperação de falhas — servidor cai→aluno envia→servidor volta→dados preservados
- [ ] 91. Auditoria de integridade — sessão/tentativa/relatório/exportação válidos; checksum automático
- [ ] 92. Classroom mobile completo — entrar por código→atividade→jogar→tentativa→feedback no iPhone
- [ ] 93. Classroom + gameplay — sala→cidade→missão→NPC→atividade→nota sem etapas manuais
- [ ] 94. Classroom + voz real — substituir `onSpeech(text)` por mic→VAD→ASR→atividade→avaliação
- [ ] 95. Classroom offline — joga offline→tentativas armazenadas→sync posterior
- [ ] 96. Sistema de autenticação — login/token/sessão/autorização contra envio falso
- [ ] 97. Monitor de saúde — alunos online, tentativas/min, falhas, latência, exportações, relatórios
- [ ] 98. Replay completo do aluno — movimentação, falhas, respostas, feedback
- [ ] 99. Certificação de produção — concorrência, mobile, voz, auth, persistência, recuperação
- [ ] 100. Integração total — professor cria→aluno iPhone→cidade 3D→missão→voz→LLM→registro→acompanha→relatório

---

## ÉPICO Voz + Cidade + NPCs (101–110, 117–125)

- [ ] 101. Voz como controle principal — "vamos para a padaria"→LLM entende→Iara responde→auto-walk→chega
- [ ] 102. Gateway LLM como dependência crítica — validar gateway online/key válida/modelo responde/NLU ok antes de qualquer teste
- [ ] 103. Teste Voice→Action — fala→destino=padaria→autoWalkTarget→Iara respondeu→andar
- [ ] 104. Captura em estado idle — screenshotPNG trava durante ação; capturar só ao parar/chegar/responder
- [ ] 105. Validação por milestones — boot→start→voz→auto-walk→porta→entrada→atividade→feedback; cada um gera screenshot+JSON
- [ ] 106. Biblioteca de comandos naturais — variações ("quero comprar pão", "me leva até a boulangerie"…) → mesmo destino
- [ ] 107. Biblioteca de intenções urbanas — ir para local, comprar, pedir info, falar com NPC, entrar/sair, concluir
- [ ] 108. Jornada completa por voz — "vamos"→chegar→"entrar"→"Bonjour"→"Je voudrais un croissant"→"Merci"→feedback
- [ ] 109. Harness voice-first — Playwright dirige só com fala simulada `onSpeech(...)`, sem WASD/hacks
- [ ] 110. Validação multimodal final — milestone: screenshot+JSON+diálogo+objetivo+posição → LLM valida cena/objetivo/avanço/NPC
- [ ] 117. Observabilidade da cidade — objetivo/NPC/missão/portal ativo por sessão
- [ ] 118. Histórico de missões — jogador→missão→tentativas→tempo→resultado
- [ ] 119. Teste integrado Cidade + Classroom — professor publica→aluno→cidade→missão→nota→relatório, sem manual
- [ ] 120. Teste integrado Cidade + Voz — fala→ASR→LLM→NPC→missão→avaliação, gravação para auditoria
- [ ] 121. Teste integrado Cidade + Mobile — iPhone→cidade→voz→classroom→relatório (simulador + hardware real)
- [ ] 122. Teste integrado Cidade + LLM multimodal — Playwright→vídeo+screenshots→LLM→score→relatório
- [ ] 123. Certificação da plataforma — cidade, NPCs, voz, LLM, classroom, mobile, CI, performance
- [ ] 124. Cenário mestre da tese — professor cria→aluno iPhone→padaria por voz→NPC→atividade→feedback→classroom→LLM valida→relatório
- [ ] 125. Meta final da plataforma — cidade+voz+LLM+classroom+mobile+validação automática+avaliação ponta a ponta

---

## ÉPICO QA Linguístico (126–140)

- [ ] 126. QA linguístico automático — conteúdo→extração→LLM revisor→relatório→correções, contínuo
- [ ] 127. Revisão multilíngue — PT/FR/EN: gramática, ortografia, naturalidade, registro, CEFR, consistência
- [ ] 128. Revisor open-source padrão — `llama-3.3-70b-versatile` principal; gpt-4o/claude só validações especiais
- [ ] 129. Auditoria CEFR — detectar A1-com-A2, A2-com-B1, B1-com-C1 → alertas
- [ ] 130. Consistência terminológica — Farmaceutica/Farmacêutica, "Cliente aguardando"/"Client en attente"…
- [ ] 131. Detector de idioma incorreto — PT em cenário FR, FR em EN, etc. (achado na padaria)
- [ ] 132. QA de NPCs — nome, descrição, falas, prompts, respostas
- [ ] 133. QA de atividades orais — pergunta, resposta esperada, acceptable variants, feedback (evitar reprovação injusta)
- [ ] 134. QA de exemplos pedagógicos — requiredForms, acceptableVariants, oralTaskGraph, teacherActivities
- [ ] 135. Benchmark de juízes — Llama 3.3 70B vs GPT-4o vs Claude: concordância
- [ ] 136. Correção assistida — erro→sugestão→revisão humana→aplicação (sem alterar conteúdo direto)
- [ ] 137. Dashboard de qualidade linguística — erros, inconsistências, CEFR, NPCs, atividades por idioma
- [ ] 138. QA linguístico na CI — todo PR: typecheck→testes→QA linguístico→relatório antes do merge
- [ ] 139. Certificação pedagógica — ortografia, gramática, naturalidade, CEFR, NPCs, atividades, feedback
- [ ] 140. Meta final QA linguístico — tudo validado por LLMs open-source + testes antes do usuário

---

## ÉPICO Pesquisa Experimental (141–155)

- [ ] 141. Benchmark humano vs tradicional
- [ ] 142. Calibração humano × LLM
- [ ] 143. Biblioteca CEFR
- [ ] 144. Autorador de missões
- [ ] 145. Detector de lacunas
- [ ] 146. Banco de tarefas
- [ ] 147. Simulador de aprendizes
- [ ] 148. Sistema de transferência
- [ ] 149. Retenção longitudinal
- [ ] 150. Currículo adaptativo
- [ ] 151. Dashboard científico
- [ ] 152. Pipeline experimental
- [ ] 153. Reprodutibilidade
- [ ] 154. Benchmark de métodos
- [ ] 155. Meta final da pesquisa

---

## ÉPICO QA da Cidade & Navegação (156–172)

- [ ] 156. Fast validation framework — `--speed 12 --iters 3`; QA de minutos para segundos
- [ ] 157. Replay determinístico — posição/rotação/objetivos/falas/resultado reproduzíveis
- [ ] 158. Biblioteca de milestones — boot/spawn/NPC/walk/porta/interior/atividade/feedback, score independente
- [ ] 159. Perfis de velocidade — Normal 1x / QA 5x / Turbo 12x / Stress 20x
- [ ] 160. Certificação de navegação — player chega? NPC alcançável? porta acessível? objetivo concluído?
- [ ] 161. Biblioteca de POIs certificados — nome, fachada, marcador, NPC, missão, feedback
- [ ] 162. Marcadores nativos — DynamicTexture falha no Native → PNG/Sprite/Atlas (Web+Native)
- [ ] 163. Biblioteca de fachadas — padaria (fachada/toldo/placa/interior), farmácia (cruz/balcão/NPC), banco…
- [ ] 164. Detector de elementos flutuantes — toldo/placa/NPC flutuando (achado na padaria)
- [ ] 165. Score de identidade visual — "isto parece uma padaria/farmácia/banco?" 0–100
- [ ] 166. Benchmark visual por local — tabela local/score
- [ ] 167. Certificação de missões — objetivo→navegação→interação→conclusão→feedback automático
- [ ] 168. Dashboard QA da cidade — missões/POIs/NPCs/fachadas/objetivos OK
- [ ] 169. Validação multimodal contínua — Playwright→screenshots→LLM→score→relatório após cada alteração
- [ ] 170. Cidade certificada — visual>90, pedagógico>90, funcional>90
- [ ] 171. Meta QA final — loop automático: cidade+voz+NPCs+missões+classroom+LLM sem testes manuais
- [ ] 172. Meta produto final — usuário fala→LLM→cidade reage→NPC→atividade→classroom→professor→LLM valida→relatório

---

## ÉPICO Motor de Avaliação Pedagógica (173–191)

- [ ] 173. Motor de avaliação pedagógica — tarefa→aluno→Cond.A(com andaime)→Cond.B(sem)→delta→veredito (valor pedagógico real)
- [ ] 174. Sistema de máscara linguística — alias para evitar conhecimento prévio do LLM (medição honesta)
- [ ] 175. Avaliação por transferência — exposição→novo contexto→sem dica→ENSINA_BEM/ENSINA_RASO/NÃO_ENSINA
- [ ] 176. Sistema de taxas — múltiplas rodadas: A%, B%, Transfer%, variância
- [ ] 177. Banco de perfis de aprendiz — LLM pequeno/médio/grande + humano iniciante/intermediário/avançado
- [ ] 178. Avaliação de andaimes — hint útil/excessivo/insuficiente; detectar tarefa que só funciona porque entregou a resposta
- [ ] 179. Sistema de retenção — separar retenção observada vs modelada (hoje é simulação)
- [ ] 180. Curvas de esquecimento — HalfLife 1/2/4 comparados
- [ ] 181. Ordenação por pré-requisitos — pré-requisito→tarefa→próxima, mesmo cruzando CEFR
- [ ] 182. Avaliação de pronúncia real — texto hoje → áudio→pronúncia→avaliação
- [ ] 183. Sistema de caveats — cada relatório: o que foi/não foi medido, limitações
- [ ] 184. Biblioteca de tarefas pedagógicas — objetivo, CEFR, vocabulário, andaime, transferência, retenção
- [ ] 185. Autorador de OralTaskGraphs — gerar NPC→pergunta→resposta→feedback a partir do currículo
- [ ] 186. Cidade pedagógica mensurável — rio-mobile sem tarefas → oralTaskGraphs+teacherActivities+avaliação
- [ ] 187. Simulador experimental — cidade→missão→aluno virtual→avaliação→relatório, sem humano
- [ ] 188. Benchmark de ensinar — tarefa A vs B vs C: qual ensina melhor
- [ ] 189. Dashboard de pesquisa — delta, transferência, retenção, andaime, CEFR, tempo; exportável
- [ ] 190. Meta final de avaliação — evidência de aprendizagem, não só conclusão de tarefas
- [ ] 191. Meta científica final — cidade+NPCs+voz+LLM+classroom+CEFR+transferência+retenção+pesquisa integrados

---

## ÉPICO Aprendizagem, Transferência & Retenção (192–211)

- [ ] 192. Medição de aprendizagem real — conclusão de tarefa ≠ aprendizagem
- [ ] 193. Near-transfer — aprendeu em A → usa em B (padaria→cafeteria, farmácia→hospital)
- [ ] 194. Far-transfer — aprendeu estrutura → aplica em situação nova ("Je voudrais…" restaurante→hotel→loja)
- [ ] 195. Detector ENSINA_RASO — completa mas não retém → prioridade alta
- [ ] 196. Banco de andaimes — fraco/adequado/excessivo por atividade
- [ ] 197. Benchmark de transferência — tabela tarefa/transferência
- [ ] 198. Sistema de variância — 3/10/30 execuções: média, desvio padrão, intervalo
- [ ] 199. Banco de perfis de LLM — 8B/70B/pequeno/grande como aprendizes simulados
- [ ] 200. Benchmark humano × LLM — aluno humano vs aluno LLM na mesma cidade
- [ ] 201. Sistema de calibração — dados reais de alunos calibram retenção/transferência/dificuldade
- [ ] 202. Banco de dados de estudos — atividade, resultado, modelo, retenção, transferência
- [ ] 203. Avaliação de currículo — "este currículo ensina?" não só "esta atividade funciona?"
- [ ] 204. Evolução de aprendizagem — dia 1/7/30/90 por aluno
- [ ] 205. Mapa de conhecimento — vocabulário/gramática/funções comunicativas como grafo
- [ ] 206. Sistema de recomendação — fraqueza→nova missão→reforço automático
- [ ] 207. Motor de pesquisa — hipótese→grupo controle→grupo experimental→resultados→relatório
- [ ] 208. Reprodutibilidade completa — modelo, prompt, versão, atividade, resultado
- [ ] 209. Dashboard científico — retenção, transferência, aprendizagem, dificuldade, engajamento
- [ ] 210. Meta final experimental — cidade→voz→LLM→NPCs→atividades→transferência→retenção→aprendizagem→relatórios científicos
- [ ] 211. Meta final da tese — plataforma completa de ensino + experimentação científica reproduzível

---

## ÉPICO Cidade Completa do Dia a Dia (212–220)

- [ ] 212. Casa → Padaria
- [ ] 213. Casa → Farmácia
- [ ] 214. Casa → Restaurante
- [ ] 215. Casa → Banco
- [ ] 216. Casa → Hotel
- [ ] 217. Casa → Transporte
- [ ] 218. Casa → Mercado
- [ ] 219. Dia completo na cidade — casa→padaria→transporte→banco→restaurante→farmácia→casa
- [ ] 220. **Missão final integrada** — professor cria→aluno iPhone→padaria por voz→NPC→atividade→feedback→classroom registra→professor acompanha→LLM valida→relatório científico

---

## ÉPICO 11 — Movimento Procedural & Animação (221–239)

> Base: Procedural Gait System. (Renumerado de 212–230 do bloco original para não
> colidir com a Cidade Completa.)

- [ ] 221. Procedural Gait System — caminhada procedural completa: movimento→IK→foot lock→passada natural
- [ ] 222. Foot lock — evitar patinação, deslizamento, pés flutuando
- [ ] 223. Ground adaptation — pé ao terreno: subida, descida, inclinação, escadas
- [ ] 224. Dynamic stride length — velocidade → comprimento da passada automático
- [ ] 225. Procedural sway — balanço corporal, rotação do tronco, movimento dos ombros
- [ ] 226. Terrain-aware locomotion — asfalto, areia, calçada, escadas
- [ ] 227. NPC movement quality — naturalidade, estabilidade, patinação (todos os NPCs)
- [ ] 228. Hero movement quality — separar NPCs / herói / jogador, métricas próprias
- [ ] 229. Sistema de IK completo — pernas, braços, cabeça, olhos
- [ ] 230. Olhar procedural — NPCs olham para jogador / objetos / destino
- [ ] 231. Gestos procedurais — durante diálogo: apontar, acenar, cumprimentar, agradecer
- [ ] 232. Biblioteca de comportamentos — parado, andando, correndo, esperando, conversando
- [ ] 233. Sistema de multidão — centenas de NPCs com movimento procedural × FPS
- [ ] 234. Benchmark de locomoção — animação pura vs IK vs procedural vs híbrido
- [ ] 235. Certificação de movimento — sem patinação, sem clipping, sem pés flutuando
- [ ] 236. QA de animação — Playwright→vídeo→LLM multimodal→score
- [ ] 237. Biblioteca de terrenos — rua, praia, escada, morro, interior
- [ ] 238. Movimento do jogador — gap: herói ✓ / jogador ✗ → levar Procedural Gait ao avatar do jogador
- [ ] 239. Meta final de movimento — NPC→IK→foot lock→procedural gait→terreno→movimento natural, com validação visual + score de realismo

---

## Visão Final

> Cidade 3D + NPCs + Voz + LLM + Classroom + Mobile + QA Visual + QA Linguístico +
> QA Pedagógico + Transferência + Retenção + Pesquisa Experimental + Reprodutibilidade.
>
> Plataforma completa de aprendizagem de línguas baseada em cidade 3D, interação por
> voz, agentes de IA e avaliação científica automatizada. Marco final do doutorado.
