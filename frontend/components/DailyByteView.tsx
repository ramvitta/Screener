"use client";

import { useEffect, useState, type ReactNode } from "react";
import AppHeader from "./AppHeader";
import { fetchDailyByte } from "@/lib/api";
import { sentimentTone, directionTone, shiftDate } from "@/lib/byteFormat";
import type { DailyByteAnalysis, DailyByteDigestResponse } from "@/lib/types";

function Badge({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <span className="rounded-[5px] px-2 py-[3px] text-[10px] font-bold" style={{ background: bg, color }}>
      {label}
    </span>
  );
}

function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-[10px] border p-4" style={{ background: "#12161C", borderColor: "#1F2630" }}>
      <div className="mb-3 flex items-center justify-between">
        <div className="text-xs font-semibold text-[#C7CDD6]">{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

function LevelsCard({ label, analysis }: { label: string; analysis: DailyByteAnalysis | null }) {
  const level = analysis?.[`${label.toLowerCase()}_level` as keyof DailyByteAnalysis];
  const support = (analysis?.[`${label.toLowerCase()}_support` as keyof DailyByteAnalysis] as number[]) ?? [];
  const resistance = (analysis?.[`${label.toLowerCase()}_resistance` as keyof DailyByteAnalysis] as number[]) ?? [];
  const targets = (analysis?.[`${label.toLowerCase()}_targets` as keyof DailyByteAnalysis] as number[]) ?? [];
  const hasAny = level != null || support.length || resistance.length || targets.length;

  if (!hasAny) return null;

  return (
    <div className="rounded-[9px] border p-3" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
      <div className="mb-2 text-[10px] font-semibold text-[#8B96A5]">{label.toUpperCase()}</div>
      {level != null && <div className="mono text-lg font-bold text-[#E8EAED]">{(level as number).toLocaleString("en-IN")}</div>}
      <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <div className="text-[9px] text-[#5B6675]">SUPPORT</div>
          <div className="mono text-[#3DD68C]">{support.length ? support.join(", ") : "—"}</div>
        </div>
        <div>
          <div className="text-[9px] text-[#5B6675]">RESISTANCE</div>
          <div className="mono text-[#F85149]">{resistance.length ? resistance.join(", ") : "—"}</div>
        </div>
        <div>
          <div className="text-[9px] text-[#5B6675]">TARGETS</div>
          <div className="mono text-[#F0B429]">{targets.length ? targets.join(", ") : "—"}</div>
        </div>
      </div>
    </div>
  );
}

export default function DailyByteView() {
  const [selected, setSelected] = useState<string | null>(null);
  const [byte, setByte] = useState<DailyByteDigestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const b = await fetchDailyByte(selected ?? undefined);
        if (!cancelled) {
          setByte(b);
          if (selected == null) setSelected(b.date);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load the daily byte");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const tone = sentimentTone(byte?.market_bias);
  const sources = byte?.sources ?? [];
  const validSources = sources.filter((s) => s.is_valid && s.analysis);

  // Aggregate detailed fields across validated analyses.
  const nifty = validSources.find((s) => s.analysis?.nifty_level != null || (s.analysis?.nifty_support?.length ?? 0) > 0)?.analysis ?? null;
  const banknifty =
    validSources.find((s) => s.analysis?.banknifty_level != null || (s.analysis?.banknifty_support?.length ?? 0) > 0)?.analysis ?? null;
  const events = validSources.flatMap((s) => s.analysis?.events ?? []);
  const globalCues = validSources.flatMap((s) => s.analysis?.global_cues ?? []);
  const sectors = Array.from(new Set(validSources.flatMap((s) => s.analysis?.sectors ?? [])));
  const warnings = validSources.flatMap((s) => s.analysis?.warnings ?? []);

  const hasContent = !!(byte && (byte.summary || byte.top_symbols.length || sources.length));
  const current = selected ?? byte?.date ?? "";

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      <AppHeader />
      <div className="min-h-0 flex-grow overflow-y-auto px-6 py-5">
        <div className="flex flex-col gap-5">
          <div className="flex items-end justify-between">
            <div>
              <h1 className="text-xl font-semibold">Daily Byte</h1>
              <p className="mt-1 text-sm text-[#8B96A5]">
                The morning brief distilled from {sources.length} Telegram channels, analysed by the LLM.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => current && setSelected(shiftDate(current, -1))}
                disabled={!current}
                className="rounded-[7px] border border-[#232C36] px-3 py-1 text-xs text-[#A7AFBA] hover:border-[#2E3844] disabled:opacity-40"
              >
                ← Prev
              </button>
              <span className="mono rounded-[7px] border border-[#232C36] bg-[#12161C] px-3 py-1 text-xs font-bold text-[#C7CDD6]">
                {current || "—"}
              </span>
              <button
                onClick={() => current && setSelected(shiftDate(current, 1))}
                disabled={!current || current === byte?.date}
                className="rounded-[7px] border border-[#232C36] px-3 py-1 text-xs text-[#A7AFBA] hover:border-[#2E3844] disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>

          {error && (
            <div className="rounded-[8px] border border-[#F85149]/40 bg-[#F85149]/10 p-3 text-xs text-[#F85149]">{error}</div>
          )}

          {loading ? (
            <div className="rounded-[10px] border p-6 text-sm text-[#5B6675]" style={{ background: "#12161C", borderColor: "#1F2630" }}>
              Loading…
            </div>
          ) : !hasContent ? (
            <div className="rounded-[10px] border p-6" style={{ background: "#12161C", borderColor: "#1F2630" }}>
              <Badge label="EMPTY" color="#8B96A5" bg="#1B2129" />
              <p className="mt-2 text-sm text-[#8B96A5]">
                No daily byte for {current}. The Telegram worker runs once each morning (09:15 IST) and the LLM analysis
                only appears once both are configured.
              </p>
            </div>
          ) : (
            <>
              {/* Verdict + summary */}
              <Section
                title="MARKET BIAS"
                right={<Badge label={tone.label} color={tone.color} bg={tone.bg} />}
              >
                {byte?.summary ? (
                  <p className="text-sm leading-relaxed text-[#C7CDD6]">{byte.summary}</p>
                ) : (
                  <p className="text-xs text-[#5B6675]">No validated summary — see the source breakdown below.</p>
                )}
                <p className="mt-2 text-[10px] text-[#5B6675]">
                  AI-generated from Telegram messages · not investment advice.
                </p>
              </Section>

              {/* Key levels */}
              {(nifty || banknifty) && (
                <Section title="KEY LEVELS">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <LevelsCard label="NIFTY" analysis={nifty} />
                    <LevelsCard label="BANKNIFTY" analysis={banknifty} />
                  </div>
                </Section>
              )}

              {/* Symbol calls */}
              {byte!.top_symbols.length > 0 && (
                <Section title={`SYMBOL CALLS (${byte!.top_symbols.length})`}>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {byte!.top_symbols.map((s, i) => (
                      <div key={`${s.ticker}-${i}`} className="rounded-[9px] border p-3" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
                        <div className="flex items-center justify-between">
                          <span className="mono text-sm font-bold text-[#E8EAED]">{s.ticker}</span>
                          <Badge
                            label={(s.direction ?? "—").toUpperCase()}
                            color={directionTone(s.direction)}
                            bg={s.direction === "long" ? "#173324" : s.direction === "short" ? "#2A171A" : "#1B2129"}
                          />
                        </div>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                          <div>
                            <div className="text-[9px] text-[#5B6675]">TARGET</div>
                            <div className="mono text-[#F0B429]">{s.target != null ? s.target.toLocaleString("en-IN") : "—"}</div>
                          </div>
                          <div>
                            <div className="text-[9px] text-[#5B6675]">STOP LOSS</div>
                            <div className="mono text-[#F85149]">{s.stop_loss != null ? s.stop_loss.toLocaleString("en-IN") : "—"}</div>
                          </div>
                          <div className="col-span-2">
                            <div className="text-[9px] text-[#5B6675]">HORIZON</div>
                            <div className="text-[#A7AFBA]">{s.horizon || "—"}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {/* Events / global cues / sectors */}
              {(events.length > 0 || globalCues.length > 0 || sectors.length > 0) && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                  {events.length > 0 && (
                    <Section title="EVENTS">
                      <ul className="flex flex-col gap-1.5">
                        {events.map((e, i) => (
                          <li key={i} className="text-xs text-[#A7AFBA]">
                            <span className="font-semibold text-[#C7CDD6]">{e.name}</span>
                            {e.date ? <span className="mono ml-1 text-[#5B6675]">{e.date}</span> : null}
                            {e.impact ? <span className="ml-1 text-[#8B96A5]">· {e.impact}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </Section>
                  )}
                  {globalCues.length > 0 && (
                    <Section title="GLOBAL CUES">
                      <ul className="flex flex-col gap-1.5">
                        {globalCues.map((g, i) => (
                          <li key={i} className="text-xs text-[#A7AFBA]">
                            <span className="font-semibold text-[#C7CDD6]">{g.instrument}</span>
                            {g.direction ? (
                              <span className="ml-1" style={{ color: directionTone(g.direction) }}>
                                {g.direction.toUpperCase()}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </Section>
                  )}
                  {sectors.length > 0 && (
                    <Section title="SECTORS">
                      <div className="flex flex-wrap gap-1.5">
                        {sectors.map((s, i) => (
                          <span key={i} className="rounded-[5px] bg-[#1B2129] px-2 py-[3px] text-[11px] text-[#A7AFBA]">
                            {s}
                          </span>
                        ))}
                      </div>
                    </Section>
                  )}
                </div>
              )}

              {/* Warnings */}
              {warnings.length > 0 && (
                <div className="rounded-[8px] border border-[#F0B429]/40 bg-[#F0B429]/10 p-3">
                  <div className="mb-1 text-[10px] font-bold text-[#F0B429]">CAVEATS</div>
                  <ul className="flex flex-col gap-1">
                    {warnings.map((w, i) => (
                      <li key={i} className="text-xs text-[#A7AFBA]">· {w}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Source breakdown */}
              <Section title={`SOURCES (${sources.length})`}>
                <div className="flex flex-col gap-3">
                  {sources.map((s, i) => (
                    <div key={i} className="rounded-[9px] border p-3" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
                      <div className="mb-1.5 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold text-[#C7CDD6]">{s.chat_username || s.chat_title || `#${s.group_slot}`}</span>
                          {s.posted_at ? <span className="mono text-[10px] text-[#5B6675]">{s.posted_at.slice(0, 16)}</span> : null}
                        </div>
                        <div className="flex items-center gap-1.5">
                          {s.analysis?.confidence != null && (
                            <Badge label={`${Math.round((s.analysis.confidence ?? 0) * 100)}%`} color="#5B9BD5" bg="#16222F" />
                          )}
                          <Badge
                            label={s.is_valid ? "VALIDATED" : "UNVERIFIED"}
                            color={s.is_valid ? "#3DD68C" : "#8B96A5"}
                            bg={s.is_valid ? "#173324" : "#1B2129"}
                          />
                        </div>
                      </div>
                      {s.summary ? (
                        <p className="text-xs leading-relaxed text-[#A7AFBA]">{s.summary}</p>
                      ) : (
                        <p className="text-xs text-[#5B6675]">{s.is_valid ? "No summary produced." : "LLM extraction failed the faithfulness check — no summary shown."}</p>
                      )}
                    </div>
                  ))}
                </div>
              </Section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
