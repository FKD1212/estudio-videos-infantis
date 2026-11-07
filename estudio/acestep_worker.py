"""Roda DENTRO do ambiente do ACE-Step (Python 3.12), chamado por estudio/musica.py: o ACE-Step
não roda no Python do projeto. Lê um pedido JSON, gera as faixas (instrumentais, ou cantadas
quando o pedido traz a letra) e grava o resultado em JSON. Só usa a biblioteca padrão e o
próprio ACE-Step."""
import json
import os
import sys
from pathlib import Path


def main(caminho_pedido: str) -> None:
    pedido = json.loads(Path(caminho_pedido).read_text(encoding="utf-8"))
    raiz = pedido["raiz_acestep"]
    os.chdir(raiz)
    sys.path.insert(0, raiz)
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    from acestep.llm_inference import LLMHandler

    dit = AceStepHandler()
    mensagem, ok = dit.initialize_service(project_root=raiz, config_path=pedido["modelo"], device="cuda",
                                          offload_to_cpu=pedido.get("offload", False))
    if not ok:
        raise SystemExit(f"ACE-Step não inicializou: {mensagem}")
    # O planejador (modelo de linguagem de 5 Hz) só é carregado para canções: sem ele, a voz canta
    # bonito mas não segue a letra. Nas trilhas instrumentais, estilo, andamento e tom já vêm definidos.
    planejador = LLMHandler()
    if any(f.get("letra") for f in pedido["faixas"]):
        mensagem, ok = planejador.initialize(
            checkpoint_dir=str(Path(raiz) / "checkpoints"), lm_model_path=pedido.get("planejador", "acestep-5Hz-lm-1.7B"),
            backend="pt", device="cuda", offload_to_cpu=pedido.get("offload", False))
        if not ok:
            raise SystemExit(f"O planejador do ACE-Step não inicializou: {mensagem}")

    pasta = Path(pedido["pasta_saida"])
    resultados = []
    for faixa in pedido["faixas"]:
        letra = faixa.get("letra") or ""  # com letra, canção cantada (Shorts de música); sem, instrumental
        params = GenerationParams(
            task_type="text2music", caption=faixa["descricao"], lyrics=letra or "[Instrumental]",
            instrumental=not letra, duration=faixa["duracao"], bpm=faixa["bpm"], keyscale=faixa["tom"],
            timesignature="4", vocal_language=faixa.get("idioma_voz", "en") if letra else "unknown",
            seed=faixa["semente"], inference_steps=8, shift=3.0,
            thinking=bool(letra), use_cot_metas=False, use_cot_caption=False, use_cot_language=False,
        )
        config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[faixa["semente"]], audio_format="wav")
        resultado = generate_music(dit, planejador, params, config, save_dir=str(pasta))
        if not resultado.success or not resultado.audios or not resultado.audios[0].get("path"):
            resultados.append({"nome": faixa["nome"], "erro": resultado.error or "nenhum áudio gerado"})
            continue
        destino = pasta / f"{faixa['nome']}.wav"
        Path(resultado.audios[0]["path"]).replace(destino)
        resultados.append({"nome": faixa["nome"], "arquivo": str(destino)})
        print(f"faixa pronta: {faixa['nome']}", flush=True)
    Path(pedido["resultado"]).write_text(json.dumps(resultados, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
