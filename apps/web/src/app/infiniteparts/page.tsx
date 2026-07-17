"use client";

import { Suspense, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls, Grid } from "@react-three/drei";
import { ProductShell } from "@/components/ProductShell";
import { api } from "@/lib/api";

interface PartParameters {
  name: string;
  units: string;
  length: number;
  width: number;
  height: number;
  hole_diameter: number;
  wall_thickness: number;
  tolerance: number;
  features: string[];
}
interface PartResponse {
  id: string;
  parameters: PartParameters;
  notice: string;
}

function PartMesh({ p }: { p: PartParameters }) {
  // Normalize to a comfortable viewport scale.
  const s = 1 / Math.max(p.length, p.width, p.height, 1);
  const l = p.length * s;
  const w = p.width * s;
  const h = p.height * s;
  const r = (p.hole_diameter / 2) * s;
  return (
    <group>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[l, h, w]} />
        <meshStandardMaterial color="#e0a034" metalness={0.6} roughness={0.35} />
      </mesh>
      {r > 0 && (
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[r, r, h * 1.05, 32]} />
          <meshStandardMaterial color="#0b0e1a" />
        </mesh>
      )}
    </group>
  );
}

export default function InfinitePartsPage() {
  const [prompt, setPrompt] = useState("");
  const [data, setData] = useState<PartResponse | null>(null);
  const [params, setParams] = useState<PartParameters | null>(null);
  const [loading, setLoading] = useState(false);

  async function generate() {
    setLoading(true);
    const res = await api<PartResponse>("/infiniteparts", {
      method: "POST",
      json: { prompt },
    });
    setData(res);
    setParams(res.parameters);
    setLoading(false);
  }

  function update(key: keyof PartParameters, value: number) {
    setParams((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  return (
    <ProductShell
      title="InfiniteParts"
      subtitle="Describe a mechanical part; get validated parametric dimensions and a live 3D preview."
    >
      <div className="flex gap-3">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. 'a 60mm mounting bracket with an 8mm bolt hole'"
          className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none"
        />
        <button
          onClick={generate}
          disabled={!prompt.trim() || loading}
          className="rounded-xl bg-brand-500 px-6 py-3 font-medium text-indigoblack hover:bg-brand-300 disabled:opacity-50"
        >
          {loading ? "Designing…" : "Generate"}
        </button>
      </div>

      {params && data && (
        <>
          <div className="mt-8 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
            <div className="h-[360px] overflow-hidden rounded-2xl border border-white/10 bg-black/40">
              <Canvas shadows camera={{ position: [1.5, 1.2, 1.8], fov: 45 }}>
                <ambientLight intensity={0.6} />
                <directionalLight position={[3, 5, 2]} intensity={1.1} castShadow />
                <Suspense fallback={null}>
                  <PartMesh p={params} />
                </Suspense>
                <Grid args={[10, 10]} cellColor="#333" sectionColor="#8a5a16" infiniteGrid position={[0, -0.5, 0]} />
                <OrbitControls enablePan />
              </Canvas>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <h3 className="font-semibold text-white">{params.name}</h3>
              <p className="mb-4 text-xs text-slate-500">units: {params.units}</p>
              {(["length", "width", "height", "hole_diameter", "wall_thickness"] as const).map(
                (k) => (
                  <label key={k} className="mb-3 block">
                    <span className="flex justify-between text-sm text-slate-300">
                      <span className="capitalize">{k.replace("_", " ")}</span>
                      <span className="font-mono text-brand-300">{params[k]}</span>
                    </span>
                    <input
                      type="range"
                      min={0}
                      max={200}
                      value={params[k]}
                      onChange={(e) => update(k, Number(e.target.value))}
                      className="w-full accent-brand-500"
                    />
                  </label>
                ),
              )}
              <p className="mt-2 text-xs text-slate-500">tolerance ±{params.tolerance}{params.units}</p>
            </div>
          </div>

          <p className="mt-6 rounded-xl border border-amber-500/30 bg-amber-500/[0.06] p-4 text-sm text-amber-200">
            {data.notice}
          </p>
        </>
      )}
    </ProductShell>
  );
}
