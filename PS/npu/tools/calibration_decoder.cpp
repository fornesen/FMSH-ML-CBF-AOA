// Icraft's documented CustomDecoder interface: numerical three-input
// calibration, bypassing image color conversion and image normalization.
#include <icraft-backends/hostbackend/decode_custom.h>
#include <icraft-xrt/dev/host_device.h>
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <stdexcept>

class PhaCalibrationDecoder final:public CustomDecoder {
public:
    std::vector<xrt::Tensor> decode(const std::string& filename,const std::vector<xrt::TensorType>& types) override {
        // Icraft 3.36.1 validates three semicolon-separated entries, then
        // passes the complete joined string to a CustomDecoder. Our entries
        // deliberately point to the same three-input sample descriptor.
        const auto actual=filename.substr(0,filename.find(';'));
        std::ifstream manifest(actual);
        if(!manifest) throw std::runtime_error("Cannot open numerical calibration descriptor: "+actual);
        nlohmann::json j;manifest>>j;
        if(types.size()!=3||j.at("inputs").size()!=3) throw std::runtime_error("Calibration needs three inputs");
        std::vector<xrt::Tensor> tensors;
        for(std::size_t i=0;i<3;++i) {
            if(!types[i]->element_dtype.getStorageType().isFP32()) throw std::runtime_error("Expected FP32 calibration input");
            const auto path=std::filesystem::path(actual).parent_path()/j["inputs"][i].get<std::string>();
            std::ifstream input(path,std::ios::binary|std::ios::ate);
            if(!input || input.tellg()!=static_cast<std::streamoff>(types[i].bytes()))
                throw std::runtime_error("Calibration input size mismatch: "+path.string());
            std::vector<char> bytes(types[i].bytes());input.seekg(0);input.read(bytes.data(),bytes.size());
            if(!input) throw std::runtime_error("Incomplete calibration input");
            xrt::Tensor tensor(types[i]);tensor.mallocOn(xrt::HostDevice::MemRegion());
            tensor.write(0,bytes.data(),bytes.size());tensors.push_back(tensor);
        }
        return tensors;
    }
};
REGISTER_CUSTOM_DECODER(PhaCalibrationDecoder)
