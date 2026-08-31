// macOS 내장 Vision 프레임워크로 OCR. 설치도 다운로드도 없다.
//   swift vision_ocr.swift <이미지경로>
// 출력: 줄마다  텍스트 \t 신뢰도 \t x0,y0,x1,y1  (픽셀 좌표, 좌상단 원점)
import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("이미지를 못 읽었다\n".data(using: .utf8)!)
    exit(1)
}
let W = Double(cg.width), H = Double(cg.height)

let req = VNRecognizeTextRequest()
req.recognitionLanguages = ["ko-KR", "en-US"]
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false   // 공고 용어를 사전으로 고쳐버리면 안 된다

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([req])

for obs in (req.results ?? []) {
    guard let c = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox            // 0~1, 좌하단 원점
    let x0 = b.minX * W, x1 = b.maxX * W
    let y0 = (1 - b.maxY) * H, y1 = (1 - b.minY) * H
    print("\(c.string)\t\(String(format: "%.3f", c.confidence))\t\(Int(x0)),\(Int(y0)),\(Int(x1)),\(Int(y1))")
}
