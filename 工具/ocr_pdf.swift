import Foundation
import AppKit
import PDFKit
import Vision

// Local file OCR only; page numbers are one-based physical PDF pages.
let args = CommandLine.arguments
guard args.count == 4, let document = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    fatalError("Usage: ocr_pdf input.pdf output.json 1,2,3")
}
let numbers = args[3].split(separator: ",").compactMap { Int($0) }
var output = [[String: Any]]()
for number in numbers {
    autoreleasepool {
        guard let page = document.page(at: number - 1) else { return }
        let box = page.bounds(for: .mediaBox)
        let factor = min(3.0, 2400.0 / max(box.width, box.height))
        let image = page.thumbnail(of: NSSize(width: box.width * factor, height: box.height * factor), for: .mediaBox)
        guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        do {
            try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
            let observations = request.results ?? []
            var lines = [[String: Any]]()
            for observation in observations {
                guard let candidate = observation.topCandidates(1).first else { continue }
                let b = observation.boundingBox
                lines.append(["text": candidate.string, "confidence": Double(candidate.confidence), "bbox": [b.minX, b.minY, b.width, b.height]])
            }
            let text = lines.compactMap { $0["text"] as? String }.joined(separator: "\n")
            let mean = lines.isEmpty ? 0.0 : lines.reduce(0.0) { $0 + ($1["confidence"] as? Double ?? 0) } / Double(lines.count)
            output.append(["page": number, "text": text, "method": "macos_vision", "confidence": mean, "lines": lines, "warnings": ["OCR 自动识别；引用答案前须对照原页，尤其核对数字、术语和表格。"]])
        } catch {
            output.append(["page": number, "text": "", "method": "macos_vision", "error": String(describing: error)])
        }
    }
}
let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
try data.write(to: URL(fileURLWithPath: args[2]))
print("OCR processed \(output.count) pages")
