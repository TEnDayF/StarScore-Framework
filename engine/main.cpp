#include <muscriptor/muscriptor.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <vector>
#include <string>

int wmain(int argc, wchar_t** argv) {
    if(argc < 4) { std::cerr << "Usage: suno_engine model.gguf audio.f32 output.json [threads] [resume]\n"; return 2; }
    std::filesystem::path model(argv[1]), audio(argv[2]), output(argv[3]);
    std::ifstream in(audio,std::ios::binary|std::ios::ate);
    if(!in) return 3;
    auto bytes=in.tellg(); if(bytes<=0 || bytes%4!=0) return 3;
    std::vector<float> samples(static_cast<size_t>(bytes)/4);
    in.seekg(0); in.read(reinterpret_cast<char*>(samples.data()),bytes);
    msl::setLogCallback([](msl::LogLevel,std::string_view text){std::cerr<<text;},msl::LogLevel::Error);
    msl::LoadOptions load;
    load.on_progress=[](float p){std::cout<<"{\"stage\":\"load\",\"progress\":"<<p<<"}"<<std::endl;};
    auto t=msl::Transcriber::load(model,load);
    if(!t){std::cerr<<msl::describe(t.error())<<std::endl;return 4;}
    std::cout<<"{\"stage\":\"device\",\"backend\":\""<<t->device().backend<<"\",\"device\":\""<<t->device().name<<"\"}"<<std::endl;
    msl::TranscribeOptions opts; opts.n_threads=argc>4?std::stoi(argv[4]):6;
    auto notes=t->transcribe(samples,opts,[](const msl::TranscriptionUpdate& u){
      std::cout<<"{\"stage\":\"transcribe\",\"progress\":"<<u.progress<<",\"through\":"<<u.finalized_through<<"}"<<std::endl;return true;});
    if(!notes){std::cerr<<msl::describe(notes.error())<<std::endl;return 5;}
    std::ofstream out(output);out<<std::setprecision(9)<<"[";
    bool first=true;
    for(auto& n:*notes){if(!first)out<<",";first=false;
      out<<"{\"start\":"<<n.onset<<",\"end\":"<<n.offset<<",\"pitch\":"<<n.pitch<<",\"program\":"<<n.program<<",\"drum\":"<<(n.is_drum?"true":"false")<<",\"instrument\":\""<<msl::instrumentLabel(n.program)<<"\"}";
    }out<<"]";out.close(); if(!out)return 6;
    std::cout<<"{\"stage\":\"done\",\"notes\":"<<notes->size()<<"}"<<std::endl;return 0;
}
